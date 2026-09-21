"""Bank Recon: a page of the Recon Toolbox (app.py runs it; the design layer is ui.py).

Compares the bank CSV from the CSV Parser (source of truth) with a Sage bank
transactions export over the dates both files cover. All logic lives in
``bankrecon/``; this module only renders. There is no ``st.cache_data`` here on
purpose: the engine runs in milliseconds, and cached dataclasses are what broke
the supplier page on Streamlit Cloud (stale pickles across redeploys).

House rules: no sidebar, no page icon, no emojis, no em or en dashes, no
taglines, no branding.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from bankrecon.engine import Result, Window, auto_window, rand, reconcile
from bankrecon.export import workbook_bytes
from bankrecon.parse import SIDE_CSV, SIDE_SAGE, Parsed, ParseError, parse_file
from ui import tiles


def dmy(when: date) -> str:
    return f"{when:%d/%m/%Y}"


def money(cents: int) -> float:
    # Display only, for the grid's number column. The engine keeps cents.
    return cents / 100


AMOUNT_COL = st.column_config.NumberColumn(format="%,.2f")
DATE_COL = st.column_config.DateColumn(format="DD/MM/YYYY")


def show(df: pd.DataFrame, **config) -> None:
    st.dataframe(df, width="stretch", hide_index=True, column_config=config)


def _load(uploaded, side: str) -> Parsed | None:
    try:
        return parse_file(uploaded.getvalue(), side)
    except ParseError as exc:
        st.error(f"{uploaded.name}: {exc}")
        return None


def match_frame(matches) -> pd.DataFrame:
    rows = []
    for m in matches:
        rows.append({
            "Tier": m.tier,
            "Date": m.date,
            "CSV description": " | ".join(t.description for t in m.csv_txns),
            "CSV amount": money(m.csv_cents),
            "Sage date": " | ".join(dmy(t.date) for t in m.sage_txns)
                         if len({t.date for t in m.sage_txns}) > 1 else dmy(m.sage_txns[0].date),
            "Sage description": " | ".join(t.description for t in m.sage_txns),
            "Sage comment": " | ".join(t.comment for t in m.sage_txns if t.comment),
            "Sage reference": " | ".join(sorted({t.reference for t in m.sage_txns if t.reference})),
            "Sage total": money(m.sage_cents),
            "Note": m.note,
        })
    return pd.DataFrame(rows)


# ===========================================================================
# Render
# ===========================================================================

c1, c2 = st.columns(2)
csv_file = c1.file_uploader("Bank CSV from the CSV tool (source of truth)", type="csv", key="bank_csv")
sage_file = c2.file_uploader("Sage bank transactions export", type="csv", key="bank_sage")

if csv_file is None or sage_file is None:
    st.markdown('<div class="empty"><div class="empty__t">Upload both files to compare</div>'
                '<div class="empty__s">The Sage export needs Date, Description and Total. '
                'Its other columns are ignored.</div></div>', unsafe_allow_html=True)
    st.stop()

csv_parsed = _load(csv_file, SIDE_CSV)
sage_parsed = _load(sage_file, SIDE_SAGE)
if csv_parsed is None or sage_parsed is None:
    st.stop()

for label, parsed in (("Bank CSV", csv_parsed), ("Sage export", sage_parsed)):
    if not parsed.txns:
        reasons = "; ".join(f"row {r}: {why}" for r, why in parsed.skipped[:5])
        st.error(f"{label}: no transaction rows were read. {reasons}")
        st.stop()

auto = auto_window(csv_parsed, sage_parsed)
if auto is None:
    st.error(f"The two files share no dates. Bank CSV covers {dmy(csv_parsed.first_date)} to "
             f"{dmy(csv_parsed.last_date)}, Sage covers {dmy(sage_parsed.first_date)} to "
             f"{dmy(sage_parsed.last_date)}.")
    st.stop()

with st.expander("Compare settings", expanded=False):
    s1, s2, s3 = st.columns(3)
    start = s1.date_input("Compare from", value=auto.start, format="DD/MM/YYYY", key="bank_from")
    end = s2.date_input("Compare to", value=auto.end, format="DD/MM/YYYY", key="bank_to")
    tolerance = s3.number_input("Date tolerance (days)", min_value=0, max_value=31, value=0, step=1,
                                key="bank_tolerance",
                                help="0 compares on the exact date. A tolerance lets the same "
                                     "description and amount match a few days apart, marked for review.")
    st.caption(f"Bank CSV covers {dmy(csv_parsed.first_date)} to {dmy(csv_parsed.last_date)}. "
               f"Sage covers {dmy(sage_parsed.first_date)} to {dmy(sage_parsed.last_date)}. "
               f"Both cover {dmy(auto.start)} to {dmy(auto.end)}, so that is the default window.")

if start > end:
    st.error("Compare from is after Compare to.")
    st.stop()

result: Result = reconcile(csv_parsed, sage_parsed, Window(start, end), int(tolerance))

st.markdown(f'<div class="sec">Comparing {dmy(start)} to {dmy(end)}. '
            f'Bank CSV total {rand(result.csv_total)}, Sage total {rand(result.sage_total)}, '
            f'difference {rand(result.difference)}.</div>', unsafe_allow_html=True)

tiles([
    ("Bank CSV lines", str(len(result.csv_in)), "neutral"),
    ("Sage lines", str(len(result.sage_in)), "neutral"),
    ("Matched", str(result.matched_csv_lines), "green"),
    ("Missing in Sage", str(len(result.missing_in_sage)), "red" if result.missing_in_sage else "green"),
    ("Extra in Sage", str(len(result.extra_in_sage)), "red" if result.extra_in_sage else "green"),
    ("Difference", rand(result.difference), "red" if result.difference else "green"),
])

proof = "PASS" if result.proof_ok else "FAIL"
st.caption(f"Missing in Sage {rand(result.missing_total)} minus extra in Sage "
           f"{rand(result.extra_total)} equals the difference {rand(result.difference)}: {proof}.")
if not result.proof_ok:
    st.error("The proof failed. Do not rely on these lists. Report this with the two files.")
if result.sign_flip_hint:
    st.warning("The signs look inverted between the two files: flipping the bank CSV amounts "
               "would match more lines than the amounts as given. Check the sign convention "
               "of the export before acting on the lists.")
skipped = len(result.csv_skipped) + len(result.sage_skipped)
if skipped:
    with st.expander(f"{skipped} rows skipped while reading the files"):
        for label, rows in (("Bank CSV", result.csv_skipped), ("Sage export", result.sage_skipped)):
            for row, why in rows:
                st.write(f"{label}, row {row}: {why}")

_, dcol = st.columns([3, 1])
dcol.download_button("Download Excel", data=workbook_bytes(result),
                     file_name=f"bank_recon_{start:%Y%m%d}_{end:%Y%m%d}.xlsx",
                     mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     width="stretch")

tab_missing, tab_extra, tab_review, tab_matched, tab_outside = st.tabs([
    f"Missing in Sage ({len(result.missing_in_sage)})",
    f"Extra in Sage ({len(result.extra_in_sage)})",
    f"Review ({len(result.review)})",
    f"Matched ({len(result.matches)})",
    f"Outside window ({len(result.csv_out) + len(result.sage_out)})",
])

with tab_missing:
    st.markdown('<div class="sec">On the bank CSV, not in Sage. Capture these in Sage.</div>',
                unsafe_allow_html=True)
    if result.missing_in_sage:
        show(pd.DataFrame([{"Date": t.date, "Description": t.description, "Amount": money(t.cents)}
                           for t in result.missing_in_sage]),
             Date=DATE_COL, Amount=AMOUNT_COL)
    else:
        st.write("Every bank CSV line in the window is in Sage.")

with tab_extra:
    st.markdown('<div class="sec">In Sage, not on the bank CSV. Check these in Sage.</div>',
                unsafe_allow_html=True)
    if result.extra_in_sage:
        show(pd.DataFrame([{"Date": t.date, "Type": t.txn_type, "Selection": t.selection,
                            "Reference": t.reference, "Description": t.description,
                            "Comment": t.comment, "Total": money(t.cents), "Reconciled": t.reconciled}
                           for t in result.extra_in_sage]),
             Date=DATE_COL, Total=AMOUNT_COL)
    else:
        st.write("Every Sage line in the window is on the bank CSV.")

with tab_review:
    st.markdown('<div class="sec">Matched on amount, as a split, or on a different date. '
                'Read these before trusting the totals.</div>', unsafe_allow_html=True)
    if result.review:
        show(match_frame(result.review), **{"Date": DATE_COL, "CSV amount": AMOUNT_COL,
                                            "Sage total": AMOUNT_COL})
    else:
        st.write("Nothing to review. Every match was exact.")

with tab_matched:
    if result.matches:
        show(match_frame(result.matches), **{"Date": DATE_COL, "CSV amount": AMOUNT_COL,
                                             "Sage total": AMOUNT_COL})
    else:
        st.write("No matches in the window.")

with tab_outside:
    st.markdown('<div class="sec">Outside the compared dates. Not compared, listed so nothing is lost.</div>',
                unsafe_allow_html=True)
    o1, o2 = st.columns(2)
    o1.write(f"Bank CSV: {len(result.csv_out)} lines")
    if result.csv_out:
        with o1:
            show(pd.DataFrame([{"Date": t.date, "Description": t.description, "Amount": money(t.cents)}
                               for t in result.csv_out]), Date=DATE_COL, Amount=AMOUNT_COL)
    o2.write(f"Sage: {len(result.sage_out)} lines")
    if result.sage_out:
        with o2:
            show(pd.DataFrame([{"Date": t.date, "Description": t.description, "Total": money(t.cents)}
                               for t in result.sage_out]), Date=DATE_COL, Total=AMOUNT_COL)
