# Supplier Recon Tool

Deterministic reconciliation of Sage Business Cloud supplier ledgers against the
bank statement. It finds *why* supplier accounts don't reconcile and produces
worksheets the accountant actions **manually** in Sage.

**No AI/LLM matching. No Sage API. No writes to Sage.** The full design and the
reasons behind every rule are in [`BUILD.md`](BUILD.md); the golden numbers the
build must reproduce are in §9 there.

## What it does

Given the two Sage CSV exports (Supplier Transactions Report + Banks & Credit Cards
Report) it produces six worksheets:

- **Summary** — every supplier, closing balance, green/red.
- **Payments Needed** — unmatched invoices and the bank *Account Payment* that
  likely settled them, with a verdict tier (confident / ambiguous / none).
- **Invoices Needed** — payments with no invoice; request the invoice.
- **Cross-Supplier** — duplicate / mis-captured accounts (balance mirrors, item
  matches, payments that name another supplier).
- **Capture Typos** — same supplier, amounts within 5 cents.
- **Integrity** — recomputed vs reported closing to the cent; unrecognized rows.

Everything downloads as an Excel workbook.

## Architecture

```
app.py            Streamlit UI — the ONLY file that imports streamlit
recon/parse.py    Sage CSV -> dataclasses (both reports); integer cents
recon/aliases.py  alias derivation + text normalisation
recon/engine.py   classification, within/cross-supplier pairing
recon/match.py    bank-candidate search + scoring + verdict tiers
recon/sheets.py   Google Sheets read/write (aliases, match_log) — fails soft
recon/export.py   Excel workbook builder
tests/            fixtures per §8 trap + golden-number regression (§9)
```

Engine functions take parsed data and return plain dataclasses; the UI and the
Excel export are two renderers over the same result. `recon/` is pure stdlib except
the two leaf modules that need `gspread` (sheets) and `openpyxl` (export).

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Tick **Dev: auto-load repo CSVs** to load the two client exports sitting in this
folder, or upload your own. Enter a client name to scope learned aliases and the
match log.

## Test

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_golden.py` reproduces the §9 golden numbers against the real client
CSVs; it **skips automatically** when those files aren't present (they are
gitignored — client financial data must not be pushed to GitHub).

## Deploy (Streamlit Community Cloud)

1. `git init` and push to GitHub. `.gitignore` keeps secrets **and the client
   CSVs** out of the repo.
2. Create the app on Streamlit Community Cloud pointing at `app.py`.
3. Paste the contents of `.streamlit/secrets.toml` into the app's **Secrets**
   (`[gcp_service_account]` + `[recon] spreadsheet_id`). Set secrets **before**
   the first deploy, or redeploy after — env/secrets only reach a build when set
   before it runs.

Persistence is one Google Sheet ("Supplier Recon - Memory") shared with the service
account as Editor. If the Sheet is unreachable the recon still runs — persistence
is disabled and the app warns, it never crashes the analysis.
