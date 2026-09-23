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

When lines are missing in Sage, a second button downloads just those lines as a
CSV in the CSV Parser's own layout (`rows_to_csv_bytes` in that repo), ready to
import into Sage as a bank statement: header `Date,Description,Amount`, dates
`dd/mm/yyyy`, signed amount with two decimals (money in positive), CRLF rows,
UTF-8 without a BOM, named `Missing_in_Sage_<ddMonYYYY>_to_<ddMonYYYY>.csv` over
the lines' own dates.

## Inputs

| File | Columns used | Notes |
|---|---|---|
| Bank CSV (CSV Parser export) | Date, Description (or Details), Amount | A `#META` block above the header is skipped. Signed amounts: money in positive, money out negative. |
| Sage Bank Transactions export | Date, Description, Total, plus Reference, Type, Selection, Comment, Reconciled for display | Exclusive, VAT and the analysis columns are ignored. Total is the signed bank amount. |
| Sage Banks and Credit Cards Transactions Report | Date, Description, Reference, Transaction Type, Account / Customer / Supplier, Debit, Credit | Starts with a `sep=,` line and a two-line header cell. One section per bank account; when there are several, the page asks which one. Amount is Debit minus Credit (Debit is money in). On Supplier Payment and Customer Receipt rows the Description is the batch reference and the name sits in the Account column, so that reference groups split allocations and the name is carried as the comment. Opening and Closing Balance rows are read and the account is footed: opening plus every line must equal closing, to the cent, or the page warns. |

The two Sage layouts are told apart by the header row. Every file goes through a
proper CSV reader. The header row is found by scanning for a Date column and an
Amount, Total, or Debit and Credit columns. Rows below it whose date or amount
will not parse are counted and listed on the page, never dropped silently. Money
is integer cents end to end; the engine never compares floats.

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
| Name | Same date, same cents, descriptions differ, but with name evidence: a distinctive word (four letters or more, not a filler word like payment or transfer) shared between the bank text and Sage's description or account name, or one text found whole inside the other. Letters and digits are split apart, so `Plumblink08H35` still yields `plumblink`. This is how the Banks and Credit Cards report matches, since it names the supplier instead of quoting the bank narrative. | Matched |
| Amount only | Same date, same cents, no name evidence. The Sage line with the best evidence wins, then the most words in common, then file order. | Review |
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
bankrecon/export.py     Excel workbook (openpyxl), and the Missing in Sage import CSV.
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
