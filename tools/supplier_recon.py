"""Supplier Recon Tool - Streamlit UI (BUILD.md §6).

A page of the Recon Toolbox (app.py runs it); the design layer is ui.py. All business logic lives in ``recon/``; this
module is a renderer plus the Sheets write buttons. Two traps are guarded here:
Streamlit reruns the whole script on every interaction, so every Sheets write sits
inside an ``if st.button(...)`` branch recorded in ``st.session_state`` (write-once);
and every widget in a loop gets a unique key.

The look is a locked light "Apple" aesthetic - system typography, translucent
material cards, a segmented-control tab bar, custom stat tiles, press feedback, and
all default Streamlit chrome (menu, footer, header, sidebar) hidden. No sidebar.
"""

from __future__ import annotations

import hashlib
import html
import importlib
import sys
from dataclasses import asdict

import pandas as pd
import streamlit as st

from ui import esc, tiles

# ---- Stale-module guard (Streamlit Cloud) -----------------------------------
# Cloud keeps the server process across git-push redeploys: this script re-runs
# with NEW code while previously imported modules survive in sys.modules with
# OLD code. That skew has produced three distinct crashes here (unpicklable
# class identity, missing dataclass field, ImportError on a new name). If the
# in-memory engine's version is not the one this app.py was written against,
# purge every recon module and re-import fresh.
_EXPECTED_ENGINE_VERSION = 8

import recon.engine as _engine_mod  # noqa: E402

if getattr(_engine_mod, "ENGINE_VERSION", None) != _EXPECTED_ENGINE_VERSION:
    for _m in [m for m in list(sys.modules) if m == "recon" or m.startswith("recon.")]:
        sys.modules.pop(_m, None)
    importlib.invalidate_caches()
    import recon.engine as _engine_mod  # noqa: E402  (fresh copy)

from recon.engine import (AGING_ALERT_DAYS, CAT_GREEN, CAT_INVOICES, CAT_PAYMENTS,
                          ENGINE_VERSION, LEDGER_GREEN, LEDGER_RED, LEDGER_YELLOW,
                          EngineResult, analyze, ledger_rows)
from recon.match import (VERDICT_AMBIGUOUS, VERDICT_CONFIDENT, VERDICT_NONE,
                         account_payment_index, match_supplier)
from recon.parse import (BankReport, SupplierReport, parse_bank_report,
                         parse_supplier_report)
from recon.export import workbook_bytes
from recon import sheets as sheets_mod


VERDICT_PILL = {
    VERDICT_CONFIDENT: ("green", "Confident"),
    VERDICT_AMBIGUOUS: ("amber", "Ambiguous"),
    VERDICT_NONE: ("red", "Not found"),
}


def verdict_pill(verdict: str) -> str:
    tone, label = VERDICT_PILL[verdict]
    return f'<span class="vp vp--{tone}">{label}</span>'


def rand(cents) -> str:
    if cents is None:
        return ""
    return f"{cents / 100:,.2f}"


# ===========================================================================
# Cached compute (keyed on raw bytes - §6)
# ===========================================================================

# Cache-busting salt for the functions below. st.cache_data pickles the return
# value and keys on (function source, args); Streamlit Cloud keeps that cache in
# memory across a git-push redeploy. So when a cached return dataclass gains a
# field, an entry pickled by the old deploy unpickles WITHOUT it (pickle restores
# __dict__ directly, skipping field defaults) -> AttributeError. Passing this salt
# as an argument means bumping it changes the cache key and bypasses stale entries.
# Sourced from ENGINE_VERSION (recon/engine.py) - bump it there on any shape or
# behavior change, and keep _EXPECTED_ENGINE_VERSION at the top of this file in
# step with it.
_CACHE_VERSION = ENGINE_VERSION


@st.cache_data(show_spinner=False)
def _parse_supplier(data: bytes, version: int) -> SupplierReport:
    return parse_supplier_report(data)


@st.cache_data(show_spinner=False)
def _parse_bank(data: bytes, version: int) -> BankReport:
    return parse_bank_report(data)


@st.cache_data(show_spinner=False)
def _analyze(supplier_bytes: bytes, version: int,
             manual_patterns: dict[str, list[str]]) -> EngineResult:
    return analyze(parse_supplier_report(supplier_bytes), manual_patterns)


# ---------------------------------------------------------------------------
# Sheets (fails soft - §5)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _sheets_client():
    try:
        info = dict(st.secrets["gcp_service_account"])
        sid = st.secrets["recon"]["spreadsheet_id"]
        return sheets_mod.SheetsClient.connect(info, sid), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


@st.cache_data(ttl=600, show_spinner=False)
def _read_aliases(_client, refresh: int) -> list[dict]:
    # Return plain dicts, not AliasRow instances. st.cache_data pickles the
    # return value; a custom class pickles by module+qualname and Streamlit
    # Cloud can hold a second copy of the class object (script re-import),
    # which trips UnserializableReturnValueError even though it pickles fine
    # locally. Dicts of strings pickle by value with no class-identity check.
    if _client is None:
        return []
    try:
        return [asdict(a) for a in _client.read_aliases()]
    except Exception:  # noqa: BLE001
        return []


def _manual_patterns(aliases, client: str) -> dict[str, list[str]]:
    """supplier name -> alias patterns, scoped to this client (blank client = all)."""
    out: dict[str, list[str]] = {}
    cl = client.strip().lower()
    for a in aliases:
        row_client = (a.client or "").strip().lower()
        if row_client and row_client != cl:
            continue
        out.setdefault(a.supplier.strip(), []).append(a.alias_pattern)
    return out


# ===========================================================================
# Render
# ===========================================================================


# Sheets status is surfaced where it matters (inside the setup panel and on the
# save/log controls), not as a persistent header badge.
client_status, client_err = _sheets_client()

# ---- Setup (main body, no sidebar) ----------------------------------------
have_supplier = st.session_state.get("sup") is not None
with st.expander("Data & client", expanded=not have_supplier):
    cols = st.columns([1.2, 1, 1])
    client = cols[0].text_input("Client name", value="",
                                help="Scopes learned mapping rules and the match log.")
    sup_file = cols[1].file_uploader("Supplier Transactions Report", type="csv", key="sup")
    bank_file = cols[2].file_uploader("Banks & Credit Cards Report", type="csv", key="bank")
    if client_status is None and client_err:
        st.caption(f"Memory (Google Sheets) offline - recon still runs, saving disabled. {client_err}")


def _load(uploaded):
    if uploaded is not None:
        return uploaded.getvalue()
    return None


supplier_bytes = _load(sup_file)
bank_bytes = _load(bank_file)

if supplier_bytes is None:
    st.session_state.pop("run_sig", None)
    st.markdown(
        '<div class="empty"><div class="empty__t">Upload a Supplier Transactions Report to begin</div>'
        '<div class="empty__s">Export both reports from Sage Business Cloud and drop them in above. '
        'The bank report unlocks candidate-payment search.</div></div>',
        unsafe_allow_html=True,
    )
    st.stop()

# ---- Run control ----------------------------------------------------------
# Nothing computes until the user clicks Run, so results never appear before they
# have added the files they want (e.g. the bank report), and adding a file later
# re-enriches only on request. Keyed on a signature of the uploaded bytes: change
# a file and the last-run signature no longer matches, so Run must be clicked again.
_sig = hashlib.md5(supplier_bytes + b"|" + (bank_bytes or b"")).hexdigest()
_scope = "supplier + bank reports" if bank_bytes is not None else "supplier report only"
if st.button(f"Run reconciliation  ·  {_scope}", type="primary", width="stretch"):
    st.session_state["run_sig"] = _sig

if st.session_state.get("run_sig") != _sig:
    if bank_bytes is None:
        st.info("Supplier report loaded. Add the Banks & Credit Cards report to enrich the "
                "Payments Needed search, or click Run reconciliation to run on the supplier "
                "report alone.")
    else:
        st.info("Both reports loaded. Click Run reconciliation to analyse.")
    st.stop()

# ---- Compute --------------------------------------------------------------
# Aliases are read first because taught aliases feed the engine's cross-account
# pass (a pattern saved for supplier X that reaches Y's name links the accounts),
# not just the bank search. `manual` is a cache key of _analyze, so saving an
# alias recomputes the analysis with it - that is the learning loop.
refresh = st.session_state.setdefault("alias_refresh", 0)
_flash = st.session_state.pop("alias_flash", None)
if _flash:
    st.toast(_flash)
aliases = [sheets_mod.AliasRow(**d) for d in _read_aliases(client_status, refresh)]
manual = _manual_patterns(aliases, client)

supplier_report = _parse_supplier(supplier_bytes, _CACHE_VERSION)
engine = _analyze(supplier_bytes, _CACHE_VERSION, manual)
bank_report = _parse_bank(bank_bytes, _CACHE_VERSION) if bank_bytes is not None else None
bank_index = account_payment_index(bank_report) if bank_report else {}

matches: dict[str, list] = {}
if bank_report is not None:
    for res in engine.by_category(CAT_PAYMENTS):
        matches[res.name] = match_supplier(res, bank_index, manual.get(res.name, []))

greens = engine.by_category(CAT_GREEN)
pays = engine.by_category(CAT_PAYMENTS)
invs = engine.by_category(CAT_INVOICES)
n_integ = len(supplier_report.integrity_failures)

# ---- Tiles + download -----------------------------------------------------
tiles([
    ("Suppliers", str(len(engine.suppliers)), "neutral"),
    ("Green", str(len(greens)), "green"),
    ("Payments needed", str(len(pays)), "red"),
    ("Invoices needed", str(len(invs)), "red"),
    ("Cross-account", str(len(engine.settlements)), "neutral"),
    ("Integrity issues", str(n_integ), "green" if n_integ == 0 else "red"),
])

_, dcol = st.columns([3, 1])
dcol.download_button(
    "Download Excel workbook",
    data=workbook_bytes(client or "client", engine, matches, supplier_report, bank_report),
    file_name=f"supplier_recon_{(client or 'client').strip().replace(' ', '_') or 'client'}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)

tabs = st.tabs(["Summary", "Ledger", "Payments Needed", "Invoices Needed",
                "Cross-Account", "Cross-Supplier", "Capture Typos", "Integrity"])

# ---- Summary --------------------------------------------------------------
with tabs[0]:
    rows = []
    for r in engine.suppliers:
        rows.append({
            "Supplier": r.name,
            "Closing (R)": r.closing / 100,
            "Status": {CAT_GREEN: "Green", CAT_PAYMENTS: "Payments Needed",
                       CAT_INVOICES: "Invoices Needed"}[r.category],
            "Integrity": "OK" if r.supplier.integrity_ok else f"off by {rand(r.supplier.integrity_delta)}",
            "Notes": "; ".join(r.notes),
        })
    df = pd.DataFrame(rows).sort_values(["Status", "Supplier"])
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"Closing (R)": st.column_config.NumberColumn(format="%.2f")})

# ---- Ledger -----------------------------------------------------------------
with tabs[1]:
    st.markdown('<div class="sec">The full report, every transaction and date, graded: '
                '<b>green</b> = paid on the invoice date or up to 10 days after, or the '
                'account settles to R0 · <b>yellow</b> = same amount but needs review '
                '(paid before the invoice, or too long after, in an unsettled account) · '
                '<b>red</b> = no matching counterpart. <b>Balance</b> is the report\'s own '
                'running balance, untouched. Each account foots into a gray '
                '<b>TOTAL</b> row, and a <b>yellow description</b> means the invoice and '
                f'its payment sit more than {AGING_ALERT_DAYS} days apart - the pair stays '
                'matched, it is only there to be looked at.</div>',
                unsafe_allow_html=True)
    _names = [r.name for r in engine.suppliers]
    _pick = st.selectbox("Supplier", ["All suppliers"] + _names, key="ledger_pick")
    _shown = engine.suppliers if _pick == "All suppliers" else [engine.get(_pick)]
    lrows, _aged = [], []
    for res in _shown:
        _dt = _ct = 0
        for row in ledger_rows(res):
            t = row.txn
            _dt += t.debit or 0
            _ct += t.credit or 0
            lrows.append({
                "Supplier": res.name, "Date": t.date, "Reference": t.reference,
                "Type": t.txn_type, "Description": t.description,
                "Debit (R)": None if t.debit is None else t.debit / 100,
                "Credit (R)": None if t.credit is None else t.credit / 100,
                # the report's own running balance, carried through untouched
                "Balance (R)": None if t.balance is None else t.balance / 100,
                "Status": {LEDGER_GREEN: "MATCHED", LEDGER_YELLOW: "CHECK",
                           LEDGER_RED: "NO MATCH"}.get(row.status, ""),
                "Match": row.note,
            })
            _aged.append(row.aged)
        # The account footed, under its own Debit/Credit columns, closing balance
        # in the Balance column where the running balance ends.
        lrows.append({
            "Supplier": res.name, "Date": "", "Reference": "", "Type": "",
            "Description": "Totals", "Debit (R)": _dt / 100, "Credit (R)": _ct / 100,
            "Balance (R)": res.closing / 100, "Status": "TOTAL",
            "Match": "closing balance",
        })
        _aged.append(False)
    if lrows:
        _ldf = pd.DataFrame(lrows)
        _colors = {"MATCHED": "background-color:#d7f0dd", "CHECK": "background-color:#fdf0c8",
                   "NO MATCH": "background-color:#fadadd",
                   "TOTAL": "background-color:#e8e8ed;font-weight:600"}
        _desc_i = _ldf.columns.get_loc("Description")

        def _row_style(row):
            css = [_colors.get(row["Status"], "")] * len(row)
            if _aged[row.name]:   # >30 days apart: flag the description only
                css[_desc_i] = "background-color:#fdf0c8;font-weight:600"
            return css

        st.dataframe(_ldf.style.apply(_row_style, axis=1), width="stretch", hide_index=True,
                     column_config={
                         "Debit (R)": st.column_config.NumberColumn(format="%.2f"),
                         "Credit (R)": st.column_config.NumberColumn(format="%.2f"),
                         "Balance (R)": st.column_config.NumberColumn(format="%.2f"),
                     })
    else:
        st.info("No transactions.")

# ---- Payments Needed ------------------------------------------------------
with tabs[2]:
    if bank_report is None:
        st.info("Upload the Banks & Credit Cards Report to search for candidate payments.")
    st.markdown('<div class="sec">Unmatched invoices and the bank <b>Account Payments</b> that likely '
                'settled them. <b>Confident</b> = one candidate with name evidence · '
                '<b>Ambiguous</b> = never a top pick.</div>', unsafe_allow_html=True)
    logged = st.session_state.setdefault("logged_matches", set())

    for si, res in enumerate(pays):
        head = f"{res.name}  ·  closing R{rand(res.closing)}"
        with st.expander(head, expanded=False):
            for note in res.notes:
                st.info(note)
            if res.bulk:
                continue
            supplier_mrs = matches.get(res.name, [])
            if not supplier_mrs and bank_report is None:
                for inv in res.unmatched_invoices:
                    st.write(f"Invoice **{inv.reference or '-'}** · {inv.date} · "
                             f"R{rand(inv.credit)}: upload bank file to search.")
                continue

            for mi, m in enumerate(supplier_mrs):
                st.markdown(
                    f'<span class="inv">Invoice {esc(m.invoice.reference or "-")} · '
                    f'{esc(m.invoice.date)} · '
                    f'R{rand(m.amount_cents)}</span>&nbsp;&nbsp;{verdict_pill(m.verdict)}',
                    unsafe_allow_html=True)
                if m.note:
                    st.caption(m.note)
                if m.candidates:
                    cand_rows = [{
                        "Bank date": c.txn.date, "Account": c.txn.account,
                        "Description": c.txn.description, "Ref": c.txn.reference,
                        "Current GL": c.txn.allocation, "Evidence": ", ".join(c.matched_tokens),
                    } for c in m.candidates]
                    st.dataframe(pd.DataFrame(cand_rows), width="stretch", hide_index=True)
                if m.capped:
                    st.caption(f"(list capped - {m.total_hits} bank lines matched this amount)")

                # Confirm only makes sense with a concrete pick (confident) and a live sheet.
                if m.verdict == VERDICT_CONFIDENT and m.candidates:
                    cand = m.candidates[0]
                    key = (client.strip().lower(), res.name.lower(),
                           rand(m.amount_cents), cand.txn.reference)
                    wkey = f"log_{si}_{mi}"
                    if key in logged:
                        st.success("Logged to match_log")
                    elif client_status is None:
                        st.caption("Connect Sheets to log this match.")
                    elif not client.strip():
                        st.caption("Enter a client name (top) to log this match.")
                    elif st.button(f'Log match: reallocate from "{cand.txn.allocation}"', key=wkey):
                        entry = sheets_mod.MatchLogEntry(
                            client=client.strip(), supplier=res.name, amount=rand(m.amount_cents),
                            bank_date=cand.txn.date, bank_ref=cand.txn.reference,
                            bank_desc=cand.txn.description, current_allocation=cand.txn.allocation,
                            action=f"reallocate to {res.name}", by="app",
                        )
                        try:
                            wrote = client_status.append_match_log(entry)
                            logged.add(key)  # write-once guard regardless of dedupe outcome
                            st.success("Logged" if wrote else "Already logged (deduped)")
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Could not log: {exc}")
                st.divider()

# ---- Invoices Needed ------------------------------------------------------
with tabs[3]:
    st.markdown('<div class="sec">Payments with no matching invoice in the ledger - '
                'request the invoice from the supplier.</div>', unsafe_allow_html=True)
    rows = []
    for res in invs:
        for t in res.unmatched_payments:
            rows.append({"Supplier": res.name, "Payment Ref": t.reference,
                         "Amount (R)": t.debit / 100, "Date": t.date,
                         "Description": t.description, "Flag": "request invoice"})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                     column_config={"Amount (R)": st.column_config.NumberColumn(format="%.2f")})
    else:
        st.info("No unmatched payments.")

# ---- Cross-Account --------------------------------------------------------
with tabs[4]:
    st.markdown('<div class="sec">A <b>needed invoice</b> on one account matched to a '
                '<b>needed payment</b> on another, by exact amount. <b>Likely same vendor</b> = the '
                'two names share a distinctive word (e.g. Agrimark / Elgin Agrimark) · '
                '<b>Amount only</b> = amount matches but names do not - review before acting.</div>',
                unsafe_allow_html=True)
    _conf_label = {"name_linked": "Likely same vendor", "amount_only": "Amount only"}
    srows = [{"Confidence": _conf_label.get(s.confidence, s.confidence),
              "Invoice Supplier": s.invoice_supplier, "Invoice Ref": s.invoice_ref,
              "Invoice Date": s.invoice_date,
              "Payment Supplier": s.payment_supplier, "Payment Ref": s.payment_ref,
              "Payment Date": s.payment_date,
              "Amount (R)": s.amount_cents / 100,
              "Shared": ", ".join(s.evidence)} for s in engine.settlements]
    if srows:
        st.dataframe(pd.DataFrame(srows), width="stretch", hide_index=True,
                     column_config={"Amount (R)": st.column_config.NumberColumn(format="%.2f")})
    else:
        st.info("No cross-account matches found.")

# ---- Cross-Supplier -------------------------------------------------------
with tabs[5]:
    st.markdown('<div class="sec">Duplicate / mis-captured accounts: balance mirrors, item matches, '
                'and payments that name another supplier. Diagnosis for you, not an auto-merge.</div>',
                unsafe_allow_html=True)
    rows = [{"Kind": f.kind, "Supplier A": f.supplier_a, "Supplier B": f.supplier_b,
             "Amount (R)": (f.amount_cents / 100 if f.amount_cents is not None else None),
             "Evidence": ", ".join(f.evidence)} for f in engine.cross]
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                     column_config={"Amount (R)": st.column_config.NumberColumn(format="%.2f")})
    else:
        st.info("No cross-supplier findings.")

# ---- Capture Typos --------------------------------------------------------
with tabs[6]:
    st.markdown('<div class="sec">Same supplier, amounts within R1.00 - likely a capture typo '
                '(e.g. 983.86 vs 983.66). Surfaced for review, never silently paired.</div>',
                unsafe_allow_html=True)
    rows = []
    for res in engine.suppliers:
        for tp in res.typos:
            rows.append({"Supplier": res.name, "Invoice Ref": tp.invoice.reference,
                         "Invoice Date": tp.invoice.date, "Invoice (R)": tp.invoice.credit / 100,
                         "Payment Ref": tp.payment.reference, "Payment Date": tp.payment.date,
                         "Payment (R)": tp.payment.debit / 100, "Diff (R)": tp.diff_cents / 100})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("No near-match typos found.")

# ---- Integrity ------------------------------------------------------------
with tabs[7]:
    st.markdown('<div class="sec">Recomputed closing (opening + Σcredits - Σdebits) vs reported '
                'closing, to the cent. Any mismatch is excluded from confident matching.</div>',
                unsafe_allow_html=True)
    fails = supplier_report.integrity_failures + (bank_report.integrity_failures if bank_report else [])
    if fails:
        rows = []
        for s in supplier_report.integrity_failures:
            rows.append({"Type": "supplier", "Name": s.name, "Reported (R)": s.closing / 100,
                         "Recomputed (R)": s.recomputed_closing / 100, "Delta (R)": s.integrity_delta / 100})
        if bank_report:
            for a in bank_report.integrity_failures:
                rows.append({"Type": "bank", "Name": a.name, "Reported (R)": a.closing / 100,
                             "Recomputed (R)": a.recomputed_closing / 100, "Delta (R)": a.integrity_delta / 100})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.success("All balances reconcile to the cent.")

    st.write(f"**Unrecognized supplier rows:** {len(supplier_report.unrecognized)}")
    if bank_report is not None:
        st.write(f"**Unrecognized bank rows:** {len(bank_report.unrecognized)}")
    if supplier_report.duplicate_names:
        st.warning("Duplicate supplier names (kept separate): " + ", ".join(supplier_report.duplicate_names))

# ---- Mapping rules (main body footer; stored on the Sheet's aliases tab) ---
with st.expander("Mapping rules"):
    st.markdown('<div class="sec">Map bank text or another account name to a supplier '
                '(e.g. <b>USAVE</b> to Shoprite, or <b>Elgin Agrimark</b> to Agrimark).</div>',
                unsafe_allow_html=True)
    acols = st.columns([1, 1, 1.4, 0.7])
    a_sup = acols[0].text_input("Supplier", key="alias_sup")
    a_pat = acols[1].text_input("Description Map", key="alias_pat")
    a_notes = acols[2].text_input("Notes", key="alias_notes")
    acols[3].markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if acols[3].button("Save", key="save_alias"):
        if client_status is None:
            st.error("Sheets offline - cannot save.")
        elif not (a_sup.strip() and a_pat.strip()):
            st.error("Supplier and Description Map are required.")
        else:
            try:
                wrote = client_status.append_alias(sheets_mod.AliasRow(
                    client=client.strip(), supplier=a_sup.strip(),
                    alias_pattern=a_pat.strip(), source="manual", notes=a_notes.strip()))
                st.session_state["alias_refresh"] = refresh + 1
                st.session_state["alias_flash"] = (
                    "Mapping rule saved - matching re-run with it." if wrote
                    else "That mapping rule already existed - matching re-run anyway.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save: {exc}")

    st.markdown("<div style='height:.5rem'></div>", unsafe_allow_html=True)
    st.caption("Edited mapping rules in the sheet, or just saved one? Re-run reloads "
               "them and recomputes every match.")
    if st.button("Re-run matching with latest mapping rules", key="rerun_match"):
        st.session_state["alias_refresh"] = refresh + 1
        st.rerun()
