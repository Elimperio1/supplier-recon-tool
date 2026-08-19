# Supplier Recon Tool — Build Specification

Status: infrastructure done, app not yet built. This document is the full implementation
spec, verified against the two real Sage exports in this folder on 2026-08-17. Every
number in §9 was measured, not assumed. Follow this doc exactly; where it says
"NEVER" or "MUST", a real failure on the real data is behind it.

## 0. Context and hard constraints

- Purpose: for a client's books, find why supplier accounts don't reconcile and hand the
  accountant actionable worksheets to fix in Sage **manually**.
- **Pure deterministic code. No AI/LLM matching. No Sage API. No writes to Sage.**
- Inputs (uploaded by user, exported from Sage Business Cloud):
  1. Supplier Transactions Report (per-supplier ledger)
  2. Banks And Credit Cards Transactions Report
- Persistence: one Google Sheet ("Supplier Recon - Memory") via service account.
- Deploy target: Streamlit Community Cloud.
- Infrastructure already exists and is verified end-to-end (see §7). Do not redo it.

## 1. File layout

```
app.py                  # Streamlit UI only — no business logic
recon/
  parse.py              # Sage CSV -> dataclasses (both reports)
  engine.py             # classification, within/cross-supplier pairing
  match.py              # bank-candidate search + scoring
  aliases.py            # alias derivation + normalization
  sheets.py             # Google Sheets read/write (aliases, match_log)
  export.py             # Excel workbook builder
tests/
  fixtures/             # tiny synthetic CSVs, one per trap in §8
  test_parse.py  test_engine.py  test_match.py
requirements.txt        # streamlit, pandas, gspread, openpyxl
.streamlit/secrets.toml # EXISTS, gitignored — do not recreate, do not commit
BUILD.md                # this file
```

Engine functions take parsed data and return plain dataclasses/dicts; the UI and the
Excel export are two renderers over the same result object. No Streamlit imports
outside `app.py`.

## 2. Parsing the Sage exports (§8 traps apply to every line of this section)

Both files share these quirks:

- First line is literally `sep=,` — skip it.
- Encoding `utf-8-sig` (BOM). Afrikaans text occurs; keep unicode intact.
- The header row's first cell contains a **real newline** ("Supplier\n...Date").
  MUST use a proper CSV reader (`csv.reader`); NEVER split on newlines yourself and
  NEVER `pandas.read_csv` the raw file — the report is row-wise *sections*, not a table.
- Row grammar, per section (a supplier, or a bank account):
  1. Section header: col0 non-empty, **all other cells empty** — the supplier/account name.
  2. `Opening Balance as at:  DD/MM/YYYY`
  3. Zero or more transaction rows: col0 matches `^\d{2}/\d{2}/\d{4}$`.
  4. `Closing Balance as at:  DD/MM/YYYY`
  5. `Movement for the period` — ignore, but do NOT mistake it for a section header
     (it carries a value cell) and do NOT parse it as a transaction.
- Detect transactions ONLY by `col0` matching the date regex. Detect section headers
  ONLY by the all-other-cells-empty rule. Anything else unrecognized → count it and
  surface the count in the Integrity tab; never silently drop.

### 2.1 THE column trap (the most important rule in this file)

Sage puts every balance (opening AND closing) in the **Credit column when the balance
is a credit** and in the **Debit column when it is a debit**. A parser that reads one
column marks 31 of the 46 problem suppliers as reconciled — two-thirds of the tool's
entire reason to exist, silently green. Measured, not hypothetical.

Rule: for every balance row, read **both** columns and compute
`signed = credit − debit`. Sign convention used everywhere in this codebase:

- `closing > 0` (credit) → invoices exceed payments → **Payments Needed**
- `closing < 0` (debit) → payments exceed invoices → **Invoices Needed**
- `closing == 0` → green

### 2.2 Numbers, dates, money

- Amounts: plain digits with `.` decimals; may contain thousands separators inside the
  quoted cell; empty string = absent. Guard for parenthesized negatives even though
  not observed.
- **Represent money as integer cents** end-to-end in the engine. NEVER compare floats
  with `==`; the data itself contains cent-level capture typos (R1369.28 vs R1369.26)
  that float drift would blur into.
- Dates are **DD/MM/YYYY**. If pandas is used anywhere for display, `dayfirst=True`.
  The closing date is in the future (FY runs 01/03/2026–28/02/2027) — NEVER filter
  rows by "date ≤ today".
- Dates are **not a matching signal** anywhere: supplier payments happen months after
  invoices. Use dates only for display and sorting.

### 2.3 Supplier report columns

`Date, Reference, Transaction Type, Description, Debit, Credit, Balance`

- `Supplier Payment` rows carry amounts in **Debit**; `Supplier Invoice` rows in
  **Credit**. But do NOT switch on Transaction Type to pick the column — read both
  columns generically, so `Supplier Credit Note` / `Discount Received` /
  `Supplier Journal` (not present in this file, possible in others) parse correctly.
- Payment descriptions look like `"20260728-0003, 189 MAIN ROAD MOTOR ... ib payment"`
  — a batch reference prefix `^\d{8}-\d{4},\s*` followed by the **bank statement
  text**. This suffix is the alias-learning source (§4).
- Descriptions can be empty (SIV0007801) and can be **date-shaped** (SIV0007181's
  description is literally `16/03/2026`) — another reason detection keys on col0 only.
- Do not assume the running Balance column starts at zero or is continuous.

### 2.4 Bank report columns

`Date, Payee, Description, Reference, Transaction Type, Account/Customer/Supplier, Debit, Credit, Balance`

- **Inverted debit/credit semantics**: in this report, money OUT (payments) sits in
  the **Credit** column and money IN (receipts) in **Debit**. Verified against running
  balances. A model assuming "debit = money out" matches nothing. Do not "fix" this.
- Transaction types present: `Account Payment`, `Supplier Payment`,
  `Account Receipt`, `Customer Receipt`, `VAT Payment`.
- Column meaning shifts by type: on `Supplier Payment` rows the Description holds the
  batch ref (e.g. `20260302-0028`) and the Account column holds the supplier name; on
  `Account Payment` rows the Description holds bank text and Account holds a **GL
  account** (`Insurance`, `Bank Charges`, `5500/010 : Lening...`). Never assume a
  column means one thing across types.
- One batch ref can appear on **multiple rows** (split allocations — `20260302-0028`
  appears 3×). Candidate identity = (account, row index), never the ref alone.
- Three bank accounts in this file; candidates come from all of them; carry the
  account name through to output.

### 2.5 Integrity check (ships in the product, not scaffolding)

Per supplier: `opening + Σcredits − Σdebits` MUST equal reported closing to the cent
(0 mismatches across all 105 suppliers when parsed per §2.1). Any mismatch = parser
bug or malformed file → show that supplier in an **Integrity tab** with both numbers
and exclude it from confident matching. Never continue silently. Same check per bank
account against its running balance.

## 3. Recon engine

### 3.1 Classification

Green: `|closing| < 1 cent`. Red splits by sign per §2.1 into Payments Needed
(credit) / Invoices Needed (debit).

### 3.2 Within-supplier pairing (isolating the unmatched items)

- Build **multisets** (Counter keyed on cents) of credits and debits. Pair equal
  amounts by count. Multisets, not sets: identical amounts recur constantly
  (daily fuel, monthly fees). Leftovers = unmatched invoices / unmatched payments.
- **Near-match pass** on the leftovers: pairs with `|diff| ≤ R1.00` → "Capture
  Typos" sheet (same supplier, e.g. PAY 1369.28 vs SIV 1369.26, and 983.86 vs
  983.66). These little residuals are why several closings are off by odd cents.
  Widened from 5c on 2026-08-19 (real 20c keying errors were slipping through);
  safe because a near-match is never silently cleared - always human-reviewed.
- **Combination pass** (one payment covering N invoices): only when a side has ≤ 20
  unmatched items; try combinations up to size 4 summing exactly to an item on the
  other side; stop at first hit per target; hard runtime cap. With ~300 unmatched
  items (Ithuba Fuels) skip entirely and label the supplier
  `bulk/statement account — request supplier statement` instead. NEVER run
  unbounded subset-sum.
- Non-zero opening balance (ELI001: opening 8374.88): the residual may live in the
  prior period. If `|residual| == |opening|` (or residual persists after all passes
  and opening ≠ 0), emit `opening-balance component — request prior-period ledger`,
  and NEVER try to match the opening balance itself against transactions.

### 3.3 Cross-supplier pass (duplicate/mis-captured supplier accounts)

Run after 3.2, comparing **across** suppliers:

- Unmatched invoices of supplier A vs unmatched payments of supplier B, exact cents.
- Whole-balance mirrors: closing of A == −closing of B. Real hits in this data:
  Overstrand Munisipality +6702.21 ↔ Overberg Steel & Irrigation −6702.21;
  Hermanus Toyota +4341.30 ↔ Cybed Trading −4341.30.
- Boost score when A's payment descriptions contain B's name tokens: Agrimark's
  payments literally say `PURCHASE FROM ELGIN AGRI MARK` / `KAAP AGRI`, and
  "Elgin Agrimark" and "Kaap Agri Elgin" exist as separate supplier accounts.
- Output sheet: pair, amounts, evidence. This is diagnosis for the human, not an
  auto-merge.

### 3.4 Bank-candidate search (for Payments Needed)

Search space: bank rows with type `Account Payment` **only** (a `Supplier Payment`
row is already in some supplier's ledger — offering it re-allocates someone else's
match). Amounts from the **Credit** column (§2.4). Receipts are out of scope v1.

For each unmatched invoice amount:

1. Exact cents match against the search space.
2. Score each hit on **description evidence**: normalize both sides (uppercase, strip
   non-alphanumerics), token-overlap against the supplier's evidence set =
   name tokens + learned aliases (Sheet) + derived aliases (§4). Bank descriptions
   run words together (`STEEL AND PIPE08H24 debit card purchase`,
   `PnP Crp Grabou08H52`) — match tokens by **substring containment in the
   squashed description**, not word-boundary equality, and strip trailing
   time-stamps (`\d{2}H\d{2}`).
3. Verdict tiers:
   - `confident`: exactly one candidate with description evidence.
   - `ambiguous`: multiple candidates tie on score, or evidence only via amount →
     list ALL candidates, mark clearly, and **never mark a top pick**. Real case:
     R500.00 for Robertson Shell hits 3 bank lines (a transfer and two wage
     payments) — all wrong. Amount-only "matches" are the tool's main credibility
     risk.
   - `none`: say so explicitly (→ "invoice needs a payment captured; not found in
     this bank file").
4. **Common-amount guard**: if an amount matches > 5 bank lines (155.00 HONOURING
   FEE, 500.00 round sums), require description evidence or return `ambiguous`
   without listing all 40 lines (cap the list, say how many were cut).
5. Output per candidate: date, bank account, full description, ref, current GL
   allocation (the accountant needs to know what to reverse), score tier.

Verified real hits this design must reproduce (see §9): Pick n Pay, LMRC STIHL,
Steel & Pipes ×2, Shoprite (via USAVE alias), Cape Agricultural (Yoco).

## 4. Aliases

- **Derived per upload (no storage needed)**: from each supplier's own payment
  descriptions, strip the `^\d{8}-\d{4},\s*` batch prefix, drop generic tokens
  (stopword list: PURCHASE, FROM, PAYMENT, IB, TO, TRANSFER, DEBIT, CARD, FEE, PTY,
  LTD, EDMS, BPK, ...), keep distinctive tokens. Note: bank `Supplier Payment` rows
  teach nothing (Description is just the batch ref) — the ledger side is the
  learning source.
- **Manual/learned (Google Sheet `aliases` tab)**: `client | supplier |
  alias_pattern | source | notes`. The killer case derivation can't get:
  `USAVE` → Shoprite (USave is a Shoprite brand; bank says `USave Rivierso09H52`).
  Seed this row.
- Matching is case-insensitive on normalized text. `client` column scopes aliases —
  the same deployment serves every client's books.

## 5. Google Sheets layer (`recon/sheets.py`)

- Auth: `gspread.service_account_from_dict(st.secrets["gcp_service_account"])`;
  spreadsheet id from `st.secrets["recon"]["spreadsheet_id"]`.
- Read `aliases` once per session (`st.cache_data`, ttl ~10 min).
- Writes: append-only (`append_row`, `value_input_option="RAW"` so nothing gets
  auto-typed into dates). Two writers: save-learned-alias, log-confirmed-match to
  `match_log` (`logged_at, client, supplier, amount, bank_date, bank_ref, bank_desc,
  current_allocation, action, status, by`).
- Dedupe before write: alias key = (client, supplier, alias_pattern); match key =
  (client, supplier, amount, bank_ref).
- **Graceful degradation**: if Sheets is unreachable, the recon still runs — warn,
  disable persistence, never crash the analysis because the memory layer is down.

## 6. Streamlit app (`app.py`)

- Inputs: two file uploaders + a client-name text input (scopes aliases/log). Dev
  convenience: auto-load the two CSVs in the repo folder when present.
- Parse under `st.cache_data` keyed on file bytes.
- Tabs: **Summary** (all suppliers, closing, green/red badges) · **Payments Needed**
  (per supplier: unmatched invoices → candidates per §3.4, confirm button) ·
  **Invoices Needed** (unmatched payments, "request invoice" flag) ·
  **Cross-Supplier** · **Capture Typos** · **Integrity** (§2.5 results + count of
  unrecognized rows) · download button for the Excel workbook.
- **Rerun trap (weaker models always hit this)**: Streamlit reruns the whole script
  on every interaction. Any Sheets write MUST be triggered inside an
  `if st.button(...)` branch and recorded in `st.session_state` (write-once guard),
  or a single confirm click writes duplicate rows on every subsequent rerun.
- Give every button/widget an explicit unique `key` (loops over suppliers otherwise
  collide on auto-generated keys).

## 7. Existing infrastructure (do not rebuild)

- GCP project `supplier-recon`; Sheets + Drive APIs enabled; service account
  `supplier-recon-bot@supplier-recon.iam.gserviceaccount.com` (no IAM roles — access
  is via sheet sharing only).
- Sheet "Supplier Recon - Memory", id `1b4dgChRaAGHC6PHB1utmksLT0g5Vi7Hhz-Ise80IpFM`,
  owner Admin@elimperio.co.za, bot = Editor. Tabs `aliases`, `match_log` with headers.
- `.streamlit/secrets.toml` and `.streamlit/service_account.json` exist locally,
  gitignored. Chain verified end-to-end with gspread on 2026-08-17.
- Deploy day: push to GitHub (secrets stay out via .gitignore), create the app on
  Streamlit Community Cloud, paste the contents of `secrets.toml` into app Secrets.
  Env/secrets only reach a build when set **before** deploy — set secrets first,
  then deploy, or redeploy after.

## 8. Edge-case checklist (each one is a test fixture)

Parsing
- [ ] `sep=,` first line skipped
- [ ] BOM / `utf-8-sig`
- [ ] Multiline header cell (real newline inside quotes)
- [ ] Balance in Debit column (§2.1) — the 31-supplier trap
- [ ] Balance in Credit column
- [ ] Supplier with zero transactions, non-zero opening
- [ ] `Movement for the period` neither section-header nor transaction
- [ ] Description that is empty; description that looks like a date
- [ ] Thousands separators in amounts; guard parenthesized negatives
- [ ] Unrecognized row → counted, surfaced, not dropped
- [ ] Duplicate supplier section names → warn, keep separate
- [ ] Bank report: outflows in **Credit** column (inverted semantics)
- [ ] Bank `Supplier Payment` vs `Account Payment` column-meaning shift
- [ ] Same batch ref on multiple bank rows

Engine
- [ ] Money = integer cents everywhere; no float equality
- [ ] Multiset pairing with repeated identical amounts
- [ ] Near-match (≤5c) → Capture Typos, not silent pairing
- [ ] Combination pass bounded (≤20 items, ≤4-combos, runtime cap); bulk accounts
      labeled, not brute-forced
- [ ] Opening-balance residual flagged, never "matched"
- [ ] Cross-supplier mirror detection (A = −B)
- [ ] Ambiguous bank candidates: all listed, **no top pick on ties**
- [ ] Common-amount guard (R155/R500 style)
- [ ] Candidates only from `Account Payment` rows
- [ ] No date-based filtering or scoring anywhere

App
- [ ] Sheets down → analysis still works
- [ ] Confirm-button writes guarded against Streamlit reruns
- [ ] Unique widget keys in loops
- [ ] Excel: amounts as numbers, descriptions as text (no date coercion),
      frozen header row, red/green fills
- [ ] `dayfirst` dates in any display parsing; future-dated closing rows kept

## 9. Golden numbers — the build MUST reproduce these on the two real CSVs

Measured 2026-08-17 against `SupplierTransactionsReport (5).csv` and
`BanksAndCreditCardsTransactionsReport (2).csv`:

| Metric | Value |
|---|---|
| Suppliers parsed | **105** |
| Green (closing = 0) | **59** |
| Red, credit side → Payments Needed | **15** |
| Red, debit side → Invoices Needed | **31** |
| Integrity mismatches (recompute vs reported closing) | **0** |
| Bank lines | **1849** across **3** accounts |
| Bank types | Account Payment 1148 · Supplier Payment 523 · Account Receipt 96 · Customer Receipt 80 · VAT Payment 2 |

Named findings that must appear:

| Supplier | Amount | Expected result |
|---|---|---|
| Pick n Pay | 157.98 | confident candidate `PnP Crp Grabou... debit card purchase`, currently *Staff Welfare* |
| LMRC STIHL HELDERBERG | 103.55 | confident candidate `LMRC STIHL HEL...`, currently *Repair And Maintenance* |
| Steel & Pipes Hermanus | 210.75, 283.94 | two confident candidates `STEEL AND PIPE...` |
| Shoprite | 498.33 | candidate `USave Rivierso...` — **only** via the USAVE alias row |
| Cape Agricultural Products | 1265.00 | candidate `Yoco Cape A...` |
| Robertson Shell Service Station | 500.00 | **ambiguous** — ≥3 candidates, no top pick |
| Overstrand Munisipality ↔ Overberg Steel & Irrigation | 6702.21 | cross-supplier mirror |
| Hermanus Toyota ↔ Cybed Trading | 4341.30 | cross-supplier mirror |
| Ithuba Fuels | ~327k, 335 txns | bulk/statement label, combination pass skipped |
| ELI001 : El Imperio | opening 8374.88 | opening-balance component flagged |

A build that disagrees with any row above has a bug in that area — fix the build,
not the table (unless the CSVs themselves changed; then re-measure and update §9).
