"""Supplier Recon Tool — Streamlit UI (BUILD.md §6).

This file is the ONLY place Streamlit is imported. All business logic lives in
``recon/``; this module is a renderer plus the Sheets write buttons. Two traps are
guarded here explicitly: Streamlit reruns the whole script on every interaction, so
every Sheets write sits inside an ``if st.button(...)`` branch and is recorded in
``st.session_state`` (write-once); and every widget in a loop gets a unique key.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from recon.engine import (CAT_GREEN, CAT_INVOICES, CAT_PAYMENTS, EngineResult, analyze)
from recon.match import (VERDICT_AMBIGUOUS, VERDICT_CONFIDENT, VERDICT_NONE,
                         account_payment_index, match_supplier)
from recon.parse import (BankReport, SupplierReport, parse_bank_report,
                         parse_supplier_report)
from recon.export import workbook_bytes
from recon import sheets as sheets_mod

st.set_page_config(page_title="Supplier Recon Tool", page_icon="📒", layout="wide")

HERE = Path(__file__).parent
DEV_SUPPLIER = "SupplierTransactionsReport (5).csv"
DEV_BANK = "BanksAndCreditCardsTransactionsReport (2).csv"


def rand(cents) -> str:
    if cents is None:
        return ""
    return f"{cents / 100:,.2f}"


# ---------------------------------------------------------------------------
# Cached compute (keyed on raw bytes — §6)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _parse_supplier(data: bytes) -> SupplierReport:
    return parse_supplier_report(data)


@st.cache_data(show_spinner=False)
def _parse_bank(data: bytes) -> BankReport:
    return parse_bank_report(data)


@st.cache_data(show_spinner=False)
def _analyze(supplier_bytes: bytes) -> EngineResult:
    return analyze(parse_supplier_report(supplier_bytes))


# ---------------------------------------------------------------------------
# Sheets (fails soft — §5)
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
def _read_aliases(_client, refresh: int):
    if _client is None:
        return []
    try:
        return _client.read_aliases()
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


# ---------------------------------------------------------------------------
# Sidebar: inputs
# ---------------------------------------------------------------------------

st.title("📒 Supplier Recon Tool")
st.caption("Deterministic supplier reconciliation for Sage Business Cloud exports. "
           "No AI matching · no Sage API · worksheets you action manually.")

with st.sidebar:
    st.header("Inputs")
    client = st.text_input("Client name", value="", help="Scopes learned aliases and the match log.")
    sup_file = st.file_uploader("Supplier Transactions Report (CSV)", type="csv", key="sup")
    bank_file = st.file_uploader("Banks & Credit Cards Report (CSV)", type="csv", key="bank")
    dev_mode = st.checkbox("Dev: auto-load repo CSVs", value=not (sup_file and bank_file))

    client_status, client_err = _sheets_client()
    if client_status is not None:
        st.success("Memory (Sheets) connected")
    else:
        st.warning("Memory (Sheets) offline — recon still runs, saving disabled.")
        if client_err:
            st.caption(client_err)


def _load(uploaded, dev_name: str):
    if uploaded is not None:
        return uploaded.getvalue()
    if dev_mode and (HERE / dev_name).exists():
        return (HERE / dev_name).read_bytes()
    return None


supplier_bytes = _load(sup_file, DEV_SUPPLIER)
bank_bytes = _load(bank_file, DEV_BANK)

if supplier_bytes is None:
    st.info("Upload a Supplier Transactions Report to begin (or tick **Dev: auto-load repo CSVs**).")
    st.stop()

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

supplier_report = _parse_supplier(supplier_bytes)
engine = _analyze(supplier_bytes)
bank_report = _parse_bank(bank_bytes) if bank_bytes is not None else None
bank_index = account_payment_index(bank_report) if bank_report else {}

refresh = st.session_state.setdefault("alias_refresh", 0)
aliases = _read_aliases(client_status, refresh)
manual = _manual_patterns(aliases, client)

# Precompute bank matches for every Payments Needed supplier.
matches: dict[str, list] = {}
if bank_report is not None:
    for res in engine.by_category(CAT_PAYMENTS):
        matches[res.name] = match_supplier(res, bank_index, manual.get(res.name, []))

# ---------------------------------------------------------------------------
# Header metrics
# ---------------------------------------------------------------------------

greens = engine.by_category(CAT_GREEN)
pays = engine.by_category(CAT_PAYMENTS)
invs = engine.by_category(CAT_INVOICES)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Suppliers", len(engine.suppliers))
c2.metric("Green", len(greens))
c3.metric("Payments Needed", len(pays))
c4.metric("Invoices Needed", len(invs))
c5.metric("Integrity issues", len(supplier_report.integrity_failures))

st.download_button(
    "⬇️ Download Excel workbook",
    data=workbook_bytes(client or "client", engine, matches, supplier_report, bank_report),
    file_name=f"supplier_recon_{(client or 'client').strip().replace(' ', '_')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

tabs = st.tabs(["Summary", "Payments Needed", "Invoices Needed", "Cross-Supplier",
                "Capture Typos", "Integrity"])

# ---- Summary --------------------------------------------------------------
with tabs[0]:
    rows = []
    for r in engine.suppliers:
        rows.append({
            "Supplier": r.name,
            "Closing (R)": r.closing / 100,
            "Status": {CAT_GREEN: "🟢 Green", CAT_PAYMENTS: "🔴 Payments Needed",
                       CAT_INVOICES: "🔴 Invoices Needed"}[r.category],
            "Integrity": "OK" if r.supplier.integrity_ok else f"⚠ Δ{rand(r.supplier.integrity_delta)}",
            "Notes": "; ".join(r.notes),
        })
    df = pd.DataFrame(rows).sort_values(["Status", "Supplier"])
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"Closing (R)": st.column_config.NumberColumn(format="%.2f")})

# ---- Payments Needed ------------------------------------------------------
with tabs[1]:
    if bank_report is None:
        st.info("Upload the Banks & Credit Cards Report to search for candidate payments.")
    st.caption("Unmatched invoices and the bank Account Payments that likely settled them. "
               "Confident = one candidate with name evidence. Ambiguous = never a top pick.")
    logged = st.session_state.setdefault("logged_matches", set())

    for si, res in enumerate(pays):
        head = f"{res.name} · closing R{rand(res.closing)}"
        with st.expander(head, expanded=False):
            for note in res.notes:
                st.info(note)
            if res.bulk:
                continue
            supplier_mrs = matches.get(res.name, [])
            if not supplier_mrs and bank_report is None:
                for inv in res.unmatched_invoices:
                    st.write(f"• Invoice **{inv.reference or '—'}** R{rand(inv.credit)} — upload bank file to search.")
                continue

            for mi, m in enumerate(supplier_mrs):
                badge = {VERDICT_CONFIDENT: "✅ confident", VERDICT_AMBIGUOUS: "🟠 ambiguous",
                         VERDICT_NONE: "🚫 none"}[m.verdict]
                st.markdown(f"**Invoice {m.invoice.reference or '—'} · R{rand(m.amount_cents)}** — {badge}")
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
                    st.caption(f"(list capped — {m.total_hits} bank lines matched this amount)")

                # Confirm only makes sense with a concrete pick (confident) and a live sheet.
                if m.verdict == VERDICT_CONFIDENT and m.candidates:
                    cand = m.candidates[0]
                    key = (client.strip().lower(), res.name.lower(),
                           rand(m.amount_cents), cand.txn.reference)
                    wkey = f"log_{si}_{mi}"
                    if key in logged:
                        st.success("Logged to match_log ✓")
                    elif client_status is None:
                        st.caption("Connect Sheets to log this match.")
                    elif not client.strip():
                        st.caption("Enter a client name (sidebar) to log this match.")
                    elif st.button(f"Log match → reallocate from “{cand.txn.allocation}”", key=wkey):
                        entry = sheets_mod.MatchLogEntry(
                            client=client.strip(), supplier=res.name, amount=rand(m.amount_cents),
                            bank_date=cand.txn.date, bank_ref=cand.txn.reference,
                            bank_desc=cand.txn.description, current_allocation=cand.txn.allocation,
                            action=f"reallocate to {res.name}", by="app",
                        )
                        try:
                            wrote = client_status.append_match_log(entry)
                            logged.add(key)  # write-once guard regardless of dedupe outcome
                            st.success("Logged ✓" if wrote else "Already logged (deduped) ✓")
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Could not log: {exc}")
                st.divider()

# ---- Invoices Needed ------------------------------------------------------
with tabs[2]:
    st.caption("Payments with no matching invoice in the ledger — request the invoice from the supplier.")
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

# ---- Cross-Supplier -------------------------------------------------------
with tabs[3]:
    st.caption("Duplicate / mis-captured accounts: balance mirrors, item matches, and "
               "payments that name another supplier. Diagnosis for you, not an auto-merge.")
    rows = [{"Kind": f.kind, "Supplier A": f.supplier_a, "Supplier B": f.supplier_b,
             "Amount (R)": (f.amount_cents / 100 if f.amount_cents is not None else None),
             "Evidence": ", ".join(f.evidence)} for f in engine.cross]
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                     column_config={"Amount (R)": st.column_config.NumberColumn(format="%.2f")})
    else:
        st.info("No cross-supplier findings.")

# ---- Capture Typos --------------------------------------------------------
with tabs[4]:
    st.caption("Same supplier, amounts within 5 cents — likely a capture typo (e.g. 1369.28 vs 1369.26).")
    rows = []
    for res in engine.suppliers:
        for tp in res.typos:
            rows.append({"Supplier": res.name, "Invoice Ref": tp.invoice.reference,
                         "Invoice (R)": tp.invoice.credit / 100, "Payment Ref": tp.payment.reference,
                         "Payment (R)": tp.payment.debit / 100, "Diff (R)": tp.diff_cents / 100})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("No near-match typos found.")

# ---- Integrity ------------------------------------------------------------
with tabs[5]:
    st.caption("Recomputed closing (opening + Σcredits − Σdebits) vs reported closing, to the cent. "
               "Any mismatch is excluded from confident matching.")
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
        st.success("All balances reconcile to the cent. ✓")

    st.write(f"**Unrecognized supplier rows:** {len(supplier_report.unrecognized)}")
    if bank_report is not None:
        st.write(f"**Unrecognized bank rows:** {len(bank_report.unrecognized)}")
    if supplier_report.duplicate_names:
        st.warning("Duplicate supplier names (kept separate): " + ", ".join(supplier_report.duplicate_names))

# ---------------------------------------------------------------------------
# Learn an alias (sidebar footer)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.divider()
    st.subheader("Teach an alias")
    st.caption("Map bank text derivation can't reach (e.g. USAVE → Shoprite).")
    a_sup = st.text_input("Supplier", key="alias_sup")
    a_pat = st.text_input("Alias pattern (bank text token)", key="alias_pat")
    a_notes = st.text_input("Notes", key="alias_notes")
    if st.button("Save alias", key="save_alias"):
        if client_status is None:
            st.error("Sheets offline — cannot save.")
        elif not (a_sup.strip() and a_pat.strip()):
            st.error("Supplier and alias pattern are required.")
        else:
            try:
                wrote = client_status.append_alias(sheets_mod.AliasRow(
                    client=client.strip(), supplier=a_sup.strip(),
                    alias_pattern=a_pat.strip(), source="manual", notes=a_notes.strip()))
                st.session_state["alias_refresh"] = refresh + 1
                st.success("Alias saved ✓" if wrote else "Alias already exists ✓")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save: {exc}")
