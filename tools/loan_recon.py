"""Loan Recon: a page of the Recon Toolbox (app.py runs it; the design layer is ui.py).

Matches two companies' ledgers for an intercompany loan: a debit in one is a
credit of the same amount in the other. All logic lives in ``loanrecon/``; this
module only renders. No ``st.cache_data``, for the same reason as Bank Recon.

House rules: no sidebar, no page icon, no emojis, no em or en dashes, no
taglines, no branding.
"""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from loanrecon.engine import Result, rand, reconcile
from loanrecon.export import action_text, file_name, workbook_bytes
from loanrecon.parse import FORMATS, Ledger, ParseError, parse_ledger
from ui import esc, tiles

AMOUNT_COL = st.column_config.NumberColumn(format="%,.2f")
DATE_COL = st.column_config.DateColumn(format="DD/MM/YYYY")


def show(df: pd.DataFrame, **config) -> None:
    st.dataframe(df, width="stretch", hide_index=True, column_config=config)


def note(text: str) -> None:
    st.markdown(f'<div class="sec">{esc(text)}</div>', unsafe_allow_html=True)


def company_inputs(side: str) -> tuple[str, str, object]:
    note(f"Company {side}")
    name = st.text_input("Name", key=f"loan_name_{side}", placeholder=f"Company {side}")
    fmt = st.selectbox("Export format", list(FORMATS), key=f"loan_fmt_{side}")
    upload = st.file_uploader("Ledger CSV", type="csv", key=f"loan_file_{side}")
    return name.strip() or f"Company {side}", fmt, upload


def load(upload, fmt: str, name: str) -> Ledger | None:
    try:
        return parse_ledger(upload.getvalue(), fmt, name)
    except ParseError as exc:
        st.error(f"{upload.name}: {exc}")
        return None


def match_frame(matches, name_a: str, name_b: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "Amount": abs(m.a.cents) / 100,
        f"{name_a} date": m.a.date,
        f"{name_a} description": m.a.description,
        f"{name_b} date": m.b.date,
        f"{name_b} description": m.b.description,
        "Days apart": m.days,
    } for m in matches])


def unmatched_frame(txns, other: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "Date": t.date,
        "Description": t.description,
        "Side": "Debit" if t.cents > 0 else "Credit",
        "Amount": abs(t.cents) / 100,
        "Action": action_text(t, other),
    } for t in txns])


# ── inputs ───────────────────────────────────────────────────────────────────
col_a, col_b, col_set = st.columns([2, 2, 1], gap="large")
with col_a:
    name_a, fmt_a, file_a = company_inputs("A")
with col_b:
    name_b, fmt_b, file_b = company_inputs("B")
with col_set:
    note("Matching")
    tolerance = st.number_input("Date tolerance (days)", min_value=0, max_value=14, value=2, step=1)

if name_a.lower() == name_b.lower():
    # Column and sheet names are built from these, so they must differ.
    name_a, name_b = f"{name_a} (A)", f"{name_b} (B)"

run = st.button("Run reconciliation", type="primary", disabled=not (file_a and file_b),
                width="stretch")

# The result is kept in session state, keyed on every input, so a rerun from the
# download button or a tab click does not wipe it, and changing any input does.
signature = hashlib.sha256(repr((
    name_a, fmt_a, name_b, fmt_b, int(tolerance),
    hashlib.sha256(file_a.getvalue()).hexdigest() if file_a else None,
    hashlib.sha256(file_b.getvalue()).hexdigest() if file_b else None,
)).encode()).hexdigest()

if run:
    st.session_state.pop("loan_recon", None)
    ledger_a = load(file_a, fmt_a, name_a)
    ledger_b = load(file_b, fmt_b, name_b)
    if ledger_a and ledger_b:
        st.session_state["loan_recon"] = (signature, reconcile(ledger_a, ledger_b, int(tolerance)))

saved = st.session_state.get("loan_recon")
res: Result | None = saved[1] if saved and saved[0] == signature else None

if res is None:
    if not run:
        st.markdown(
            '<div class="empty"><div class="empty__t">Upload both ledgers</div>'
            '<div class="empty__s">Then run the reconciliation.</div></div>',
            unsafe_allow_html=True,
        )
    st.stop()

# ── results ──────────────────────────────────────────────────────────────────
for ledger in (res.a, res.b):
    for warning in ledger.warnings:
        st.warning(f"{ledger.name}: {warning}")

st.divider()
tiles([
    (f"{res.a.name} net", rand(res.a.net), "plain"),
    (f"{res.b.name} net", rand(res.b.net), "plain"),
    ("Difference", rand(res.difference), "green" if res.difference == 0 else "red"),
])
tiles([
    ("Matched", str(len(res.confirmed)), "plain"),
    ("To review", str(len(res.review)), "amber" if res.review else "plain"),
    (f"Unmatched in {res.a.name}", str(len(res.unmatched_a)), "red" if res.unmatched_a else "plain"),
    (f"Unmatched in {res.b.name}", str(len(res.unmatched_b)), "red" if res.unmatched_b else "plain"),
])

st.download_button(
    "Download workbook",
    data=workbook_bytes(res),
    file_name=file_name(res),
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

tab_review, tab_ua, tab_ub, tab_matched = st.tabs([
    f"To review · {len(res.review)}",
    f"Unmatched in {res.a.name} · {len(res.unmatched_a)}",
    f"Unmatched in {res.b.name} · {len(res.unmatched_b)}",
    f"Matched · {len(res.confirmed)}",
])

date_cols = {f"{res.a.name} date": DATE_COL, f"{res.b.name} date": DATE_COL}

with tab_review:
    if res.review:
        show(match_frame(res.review, res.a.name, res.b.name), Amount=AMOUNT_COL, **date_cols)
    else:
        note("Nothing to review.")

for tab, txns, own, other in ((tab_ua, res.unmatched_a, res.a.name, res.b.name),
                              (tab_ub, res.unmatched_b, res.b.name, res.a.name)):
    with tab:
        if txns:
            show(unmatched_frame(txns, other), Date=DATE_COL, Amount=AMOUNT_COL)
        else:
            note(f"Every {own} entry is matched.")

with tab_matched:
    if res.confirmed:
        show(match_frame(res.confirmed, res.a.name, res.b.name), Amount=AMOUNT_COL, **date_cols)
    else:
        note("No same-day matches.")
