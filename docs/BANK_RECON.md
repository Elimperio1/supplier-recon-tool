# Bank Recon

Compares a bank CSV from the CSV Parser (the source of truth) with a Sage Business
Cloud bank transactions export, over the dates both files cover, and lists what is
missing in Sage and what is extra in Sage. Deterministic Python, no AI, no network,
no writes to Sage. This file is the source of truth for how it works.

## Run

Bank Recon is a page of the Recon Toolbox (this repo). At the repo root:

```
python -m streamlit run app.py
```

Pick Bank Recon at the top of the page and upload the two files. The page shows
the compared window, six totals, and five tabs. The Excel download carries the
same lists plus both input files marked line by line with a Status column.

## Inputs

| File | Columns used | Notes |
|---|---|---|
| Bank CSV (CSV Parser export) | Date, Description (or Details), Amount | A `#META` block above the header is skipped. Signed amounts: money in positive, money out negative. |
| Sage bank transactions export | Date, Description, Total, plus Reference, Type, Selection, Comment, Reconciled for display | Exclusive, VAT and the analysis columns are ignored. Total is the signed bank amount. |

Both files go through a proper CSV reader. The header row is found by scanning
for a Date column and an Amount or Total column. Rows below it whose date or
amount will not parse are counted and listed on the page, never dropped silently.
Money is integer cents end to end; the engine never compares floats.

## Window

Only the dates both files cover are compared: from the later first date to the
earlier last date. If the Sage export ends on the 27th and the bank CSV runs to
the 1st of next month, the comparison stops on the 27th. Rows outside the window
are shown on the Outside window tab and in the marked sheets. The window and a
date tolerance can be changed under Compare settings.

## Matching

One to one on a multiset: three R155 fees against two leave exactly one unmatched.
Passes run in this order and a line is consumed by the first pass that claims it.

| Tier | Rule | Shown as |
|---|---|---|
| Exact | Same date, same cents, same normalised description. The CSV text is checked against Sage's Description and its Comment, because Sage often renames the Description to a supplier name and keeps the bank narrative in Comment. | Matched |
| Amount only | Same date, same cents, descriptions differ. The Sage line with the most words in common wins; ties go to file order. | Review |
| Split | One CSV line equals the sum of two or more Sage lines on the same date under one Reference (Sage splits one bank line into allocations). Sage lines without a reference are never grouped. The mirror case, one Sage line equal to several CSV lines with the same description and sign on the same date, is also claimed. | Review |
| Date differs | Only with a tolerance above zero. Same cents and same description within the tolerance; nearest date wins. | Review |

Whatever is left on the CSV side is Missing in Sage (capture it). Whatever is
left on the Sage side is Extra in Sage (check it). The page proves itself on every
run: missing total minus extra total must equal CSV total minus Sage total, to the
cent. A sign check also runs: if flipping the CSV amounts would pair more lines than
the amounts as given, the page warns that the two files disagree on convention.

## Layout

```
tools/bank_recon.py     Streamlit page, renders only. Design layer from ui.py.
bankrecon/parse.py      Both CSVs to transactions in integer cents.
bankrecon/engine.py     Window, matching passes, result and proof.
bankrecon/export.py     Excel workbook (openpyxl).
tests/test_bank_*.py    pytest. Fixtures are invented data (tests/fixtures/bank_recon_*.csv).
                        test_bank_golden.py runs against the two real exports when they
                        are present at the repo root and skips otherwise.
```

Client exports are gitignored (`*.csv` except `tests/fixtures/`), as are generated
workbooks. There is no `st.cache_data`: the engine runs in milliseconds and cached
dataclasses are what broke the supplier page across Streamlit Cloud redeploys.

## Tests

```
python -m pytest -q
```

## Deploy

Deploys with the toolbox (see the repo README). Bank Recon needs no secrets and
calls no network.
