"""Supplier Recon Tool - Streamlit UI (BUILD.md §6).

The ONLY file that imports Streamlit. All business logic lives in ``recon/``; this
module is a renderer plus the Sheets write buttons. Two traps are guarded here:
Streamlit reruns the whole script on every interaction, so every Sheets write sits
inside an ``if st.button(...)`` branch recorded in ``st.session_state`` (write-once);
and every widget in a loop gets a unique key.

The look is a locked light "Apple" aesthetic - system typography, translucent
material cards, a segmented-control tab bar, custom stat tiles, press feedback, and
all default Streamlit chrome (menu, footer, header, sidebar) hidden. No sidebar.
"""

from __future__ import annotations

import html
from dataclasses import asdict

import pandas as pd
import streamlit as st

from recon.engine import (CAT_GREEN, CAT_INVOICES, CAT_PAYMENTS, EngineResult, analyze)
from recon.match import (VERDICT_AMBIGUOUS, VERDICT_CONFIDENT, VERDICT_NONE,
                         account_payment_index, match_supplier)
from recon.parse import (BankReport, SupplierReport, parse_bank_report,
                         parse_supplier_report)
from recon.export import workbook_bytes
from recon import sheets as sheets_mod

st.set_page_config(page_title="Supplier Recon", layout="wide",
                   initial_sidebar_state="collapsed")

# ===========================================================================
# Design layer
# ===========================================================================

CSS = """
:root{
  --bg:#f5f5f7; --surface:rgba(255,255,255,.72); --solid:#fff;
  --border:rgba(0,0,0,.08); --hair:rgba(0,0,0,.06);
  --ink:#1d1d1f; --ink2:#6e6e73; --ink3:#86868b;
  --accent:#0071e3; --accent-press:#0060c0;
  --g-fg:#0a7d33; --g-bg:rgba(52,199,89,.14);
  --r-fg:#c1121f; --r-bg:rgba(255,59,48,.11);
  --a-fg:#8a6100; --a-bg:rgba(255,179,64,.17);
  --shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px rgba(0,0,0,.05);
  --radius:16px; --sys:-apple-system,BlinkMacSystemFont,"SF Pro Text","SF Pro Display","Inter","Segoe UI",Roboto,system-ui,sans-serif;
}
/* hide all default Streamlit chrome */
#MainMenu,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],
header[data-testid="stHeader"],footer,[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"]{display:none!important;}

.stApp{background:var(--bg);}
.stApp,.stApp p,.stApp label,.stApp span,.stApp div,.stApp input,.stApp button,
.stMarkdown,h1,h2,h3,h4{font-family:var(--sys);}
/* keep icon fonts intact */
.material-icons,.material-icons-outlined,[class*="material-symbols"],
[data-testid="stIconMaterial"]{font-family:"Material Symbols Rounded","Material Symbols Outlined","Material Icons"!important;}

.block-container{max-width:1180px;padding-top:1.6rem;padding-bottom:4rem;}

/* hero ------------------------------------------------------------------ */
.hero{display:flex;align-items:center;justify-content:space-between;gap:16px;
  margin:.2rem 0 1.4rem;}
.hero__title{font-size:2.05rem;font-weight:640;letter-spacing:-.03em;color:var(--ink);
  line-height:1.05;}

/* stat tiles ------------------------------------------------------------ */
.tiles{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin:.2rem 0 1.1rem;}
.tile{background:var(--surface);backdrop-filter:blur(20px) saturate(180%);
  -webkit-backdrop-filter:blur(20px) saturate(180%);border:1px solid var(--border);
  border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow);}
.tile__v{font-size:1.9rem;font-weight:640;letter-spacing:-.03em;color:var(--ink);line-height:1;}
.tile__l{margin-top:.5rem;font-size:.72rem;font-weight:600;letter-spacing:.055em;
  text-transform:uppercase;color:var(--ink3);}
.tile--green .tile__v{color:var(--g-fg);} .tile--red .tile__v{color:var(--r-fg);}
.tile--amber .tile__v{color:var(--a-fg);}
@media(max-width:900px){.tiles{grid-template-columns:repeat(2,1fr);}}

/* section label + inline verdict pills ---------------------------------- */
.sec{font-size:.9rem;color:var(--ink2);letter-spacing:-.01em;margin:.1rem 0 .9rem;}
.vp{display:inline-flex;align-items:center;gap:.35rem;padding:.16rem .6rem;border-radius:980px;
  font-size:.78rem;font-weight:600;letter-spacing:-.01em;}
.vp--green{color:var(--g-fg);background:var(--g-bg);}
.vp--amber{color:var(--a-fg);background:var(--a-bg);}
.vp--red{color:var(--r-fg);background:var(--r-bg);}
.inv{font-weight:600;color:var(--ink);letter-spacing:-.01em;}

/* tabs -> segmented control (Streamlit 1.61: [role=tablist] / [data-testid=stTab]) */
.stTabs [role="tablist"]{gap:4px;background:rgba(0,0,0,.05);padding:5px;border:none!important;
  border-radius:13px;display:inline-flex;flex-wrap:wrap;margin-bottom:.4rem;}
.stTabs [role="tablist"]::after,.stTabs [role="tablist"]::before{display:none!important;}
.stTabs [data-testid="stTab"]{height:auto;padding:.48rem 1.05rem;border-radius:9px;color:var(--ink2);
  font-weight:560;letter-spacing:-.01em;border:none!important;background:transparent;
  transition:color .15s ease,background .15s ease,box-shadow .15s ease;}
.stTabs [data-testid="stTab"]:hover{color:var(--ink);}
.stTabs [data-testid="stTab"][aria-selected="true"]{background:var(--solid);color:var(--ink)!important;
  box-shadow:0 1px 3px rgba(0,0,0,.14);}
.stTabs [data-testid="stTab"] p{font-weight:560;letter-spacing:-.01em;}

/* buttons --------------------------------------------------------------- */
.stButton>button,.stDownloadButton>button{border-radius:980px;border:1px solid var(--border);
  background:var(--solid);color:var(--ink);font-weight:560;letter-spacing:-.01em;
  padding:.5rem 1.1rem;transition:transform .12s ease,background .15s ease,box-shadow .15s ease;
  box-shadow:0 1px 2px rgba(0,0,0,.05);}
.stButton>button:hover,.stDownloadButton>button:hover{background:#fafafa;border-color:rgba(0,0,0,.14);}
.stButton>button:active,.stDownloadButton>button:active{transform:scale(.97);}
.stDownloadButton>button{background:var(--accent);color:#fff;border-color:transparent;}
.stDownloadButton>button:hover{background:var(--accent-press);}

/* expanders as cards ---------------------------------------------------- */
[data-testid="stExpander"]{border:1px solid var(--border);border-radius:var(--radius);
  background:var(--surface);backdrop-filter:blur(20px) saturate(180%);
  -webkit-backdrop-filter:blur(20px) saturate(180%);box-shadow:var(--shadow);
  overflow:hidden;margin-bottom:.7rem;}
[data-testid="stExpander"] summary{font-weight:590;letter-spacing:-.01em;color:var(--ink);padding:.2rem .1rem;}
[data-testid="stExpander"] summary:hover{color:var(--accent);}

/* inputs + uploader ----------------------------------------------------- */
[data-baseweb="input"],[data-baseweb="base-input"]{border-radius:10px!important;}
.stTextInput input{border-radius:10px;}
[data-testid="stFileUploaderDropzone"]{border-radius:12px;border:1px dashed rgba(0,0,0,.16);
  background:rgba(0,0,0,.02);}

/* dataframe ------------------------------------------------------------- */
[data-testid="stDataFrame"]{border:1px solid var(--border);border-radius:12px;overflow:hidden;
  box-shadow:var(--shadow);}

/* alerts ---------------------------------------------------------------- */
[data-testid="stAlert"]{border-radius:12px;border:1px solid var(--hair);}

/* empty state ----------------------------------------------------------- */
.empty{background:var(--surface);border:1px solid var(--border);border-radius:20px;
  box-shadow:var(--shadow);padding:2.4rem 2rem;text-align:center;margin-top:.5rem;}
.empty__t{font-size:1.15rem;font-weight:600;letter-spacing:-.02em;color:var(--ink);}
.empty__s{margin-top:.4rem;color:var(--ink2);font-size:.95rem;}
hr{border-color:var(--hair);}
"""


def inject_css() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s))


def hero() -> None:
    st.markdown('<div class="hero"><div class="hero__title">Supplier Recon</div></div>',
                unsafe_allow_html=True)


def tiles(items: list[tuple[str, str, str]]) -> None:
    cells = "".join(
        f'<div class="tile tile--{tone}"><div class="tile__v">{esc(value)}</div>'
        f'<div class="tile__l">{esc(label)}</div></div>'
        for label, value, tone in items
    )
    st.markdown(f'<div class="tiles">{cells}</div>', unsafe_allow_html=True)


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

inject_css()
hero()

# Sheets status is surfaced where it matters (inside the setup panel and on the
# save/log controls), not as a persistent header badge.
client_status, client_err = _sheets_client()

# ---- Setup (main body, no sidebar) ----------------------------------------
have_supplier = st.session_state.get("sup") is not None
with st.expander("Data & client", expanded=not have_supplier):
    cols = st.columns([1.2, 1, 1])
    client = cols[0].text_input("Client name", value="",
                                help="Scopes learned aliases and the match log.")
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
    st.markdown(
        '<div class="empty"><div class="empty__t">Upload a Supplier Transactions Report to begin</div>'
        '<div class="empty__s">Export both reports from Sage Business Cloud and drop them in above. '
        'The bank report unlocks candidate-payment search.</div></div>',
        unsafe_allow_html=True,
    )
    st.stop()

# ---- Compute --------------------------------------------------------------
supplier_report = _parse_supplier(supplier_bytes)
engine = _analyze(supplier_bytes)
bank_report = _parse_bank(bank_bytes) if bank_bytes is not None else None
bank_index = account_payment_index(bank_report) if bank_report else {}

refresh = st.session_state.setdefault("alias_refresh", 0)
aliases = [sheets_mod.AliasRow(**d) for d in _read_aliases(client_status, refresh)]
manual = _manual_patterns(aliases, client)

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

tabs = st.tabs(["Summary", "Payments Needed", "Invoices Needed", "Cross-Supplier",
                "Capture Typos", "Integrity"])

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

# ---- Payments Needed ------------------------------------------------------
with tabs[1]:
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
                    st.write(f"Invoice **{inv.reference or '-'}** R{rand(inv.credit)}: upload bank file to search.")
                continue

            for mi, m in enumerate(supplier_mrs):
                st.markdown(
                    f'<span class="inv">Invoice {esc(m.invoice.reference or "-")} · '
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
with tabs[2]:
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

# ---- Cross-Supplier -------------------------------------------------------
with tabs[3]:
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
with tabs[4]:
    st.markdown('<div class="sec">Same supplier, amounts within 5 cents - likely a capture typo '
                '(e.g. 1369.28 vs 1369.26).</div>', unsafe_allow_html=True)
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

# ---- Teach an alias (main body footer) ------------------------------------
with st.expander("Teach an alias"):
    st.markdown('<div class="sec">Map bank text derivation can\'t reach '
                '(e.g. <b>USAVE</b> to Shoprite).</div>', unsafe_allow_html=True)
    acols = st.columns([1, 1, 1.4, 0.7])
    a_sup = acols[0].text_input("Supplier", key="alias_sup")
    a_pat = acols[1].text_input("Alias pattern", key="alias_pat")
    a_notes = acols[2].text_input("Notes", key="alias_notes")
    acols[3].markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if acols[3].button("Save", key="save_alias"):
        if client_status is None:
            st.error("Sheets offline - cannot save.")
        elif not (a_sup.strip() and a_pat.strip()):
            st.error("Supplier and alias pattern are required.")
        else:
            try:
                wrote = client_status.append_alias(sheets_mod.AliasRow(
                    client=client.strip(), supplier=a_sup.strip(),
                    alias_pattern=a_pat.strip(), source="manual", notes=a_notes.strip()))
                st.session_state["alias_refresh"] = refresh + 1
                st.success("Alias saved" if wrote else "Alias already exists")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not save: {exc}")
