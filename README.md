# Recon Toolbox

One Streamlit app, two reconciliation tools, a segmented control at the top of
the page to pick one. No sidebar.

- **Supplier Recon**: Sage Business Cloud supplier ledgers against the bank
  statement, producing worksheets the accountant actions **manually** in Sage.
  The full design and the reasons behind every rule are in [`BUILD.md`](BUILD.md);
  the golden numbers the build must reproduce are in §9 there. The rest of this
  file describes this tool.
- **Bank Recon**: the CSV Parser's bank CSV (the source of truth) against a Sage
  bank transactions export, over the dates both files cover: what is missing in
  Sage, what is extra. Spec in [`docs/BANK_RECON.md`](docs/BANK_RECON.md).

**No AI/LLM matching. No Sage API. No writes to Sage.** Both tools.

Adding a tool: one `st.Page` line in `TOOLS` in `app.py` plus a page under
`tools/` that renders its inputs on the page, imports the design layer from
`ui.py`, and never calls `st.set_page_config` or `st.sidebar`. The CSV Parser's
loan reconciliation page is the next candidate.

## Supplier Recon: what it does

Given the two Sage CSV exports (Supplier Transactions Report + Banks & Credit Cards
Report) it produces six worksheets:

- **Summary** — every supplier, closing balance, green/red.
- **Payments Needed** — unmatched invoices and the bank *Account Payment* that
  likely settled them, with a verdict tier (confident / ambiguous / none).
- **Invoices Needed** — payments with no invoice; request the invoice.
- **Cross-Supplier** — duplicate / mis-captured accounts (balance mirrors, item
  matches, payments that name another supplier).
- **Capture Typos** — same supplier, amounts within R1.00.
- **Integrity** — recomputed vs reported closing to the cent; unrecognized rows.

Everything downloads as an Excel workbook.

## Architecture

```
app.py                   Recon Toolbox entry: page config, design layer, tool switcher, st.navigation
ui.py                    shared design layer (CSS, hero, stat tiles)
tools/supplier_recon.py  Supplier Recon page (renders only)
tools/bank_recon.py      Bank Recon page (renders only)
recon/parse.py           Sage CSV -> dataclasses (both supplier reports); integer cents
recon/aliases.py         alias derivation + text normalisation
recon/engine.py          classification, within/cross-supplier pairing
recon/match.py           bank-candidate search + scoring + verdict tiers
recon/sheets.py          Google Sheets read/write (aliases, match_log), fails soft
recon/export.py          Excel workbook builder
bankrecon/parse.py       bank CSV + Sage bank export -> transactions in integer cents
bankrecon/engine.py      window, matching passes, proof
bankrecon/export.py      Excel workbook builder
tests/                   supplier fixtures per §8 trap + golden numbers (§9);
                         test_bank_*.py for Bank Recon; test_toolbox_app.py house rules + smoke
```

Engine functions take parsed data and return plain dataclasses; the pages and the
Excel exports are renderers over the same result. `recon/` and `bankrecon/` are
pure stdlib except the leaf modules that need `gspread` (sheets) and `openpyxl`
(export). Streamlit is imported only by `app.py`, `ui.py` and `tools/`.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Pick the tool at the top of the page and upload its files. On Supplier Recon,
enter a client name to scope learned aliases and the match log.

## Test

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_golden.py` reproduces the §9 golden numbers against the real client
CSVs, and `tests/test_bank_golden.py` does the same for Bank Recon's first real
pair of exports; both **skip automatically** when those files aren't present (they
are gitignored — client financial data must not be pushed to GitHub).

## Deploy (Streamlit Community Cloud)

1. `git init` and push to GitHub. `.gitignore` keeps secrets **and the client
   CSVs** out of the repo.
2. Create the app on Streamlit Community Cloud pointing at `app.py`.
3. Paste the contents of `.streamlit/secrets.toml` into the app's **Secrets**
   (`[gcp_service_account]` + `[recon] spreadsheet_id`). Set secrets **before**
   the first deploy, or redeploy after — env/secrets only reach a build when set
   before it runs. Only Supplier Recon uses them; Bank Recon needs no secrets and
   calls no network.

Persistence is one Google Sheet ("Supplier Recon - Memory") shared with the service
account as Editor. If the Sheet is unreachable the recon still runs — persistence
is disabled and the app warns, it never crashes the analysis.
