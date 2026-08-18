# Vault handoff — Supplier Recon Tool (2026-08-18)

Carried through the `supplier-recon-tool` repo to bring the **other machine's Obsidian
vault** up to date. The two machines' vaults do **not** sync, so this file is the
transport (same pattern the practice-management repo uses for its memory handoffs).

**How to apply on the other machine:** `git pull`, then open Claude Code in that
machine's vault context and say *"apply docs/VAULT_HANDOFF.md to the vault."* Everything
below is safe to store in the company vault — no secrets, no credentials, no client
figures. Paths are relative to the vault root
(`.../El Imperio Accountants/Vault/Elimperio's Vaullt/`).

If a target file already has some of this (e.g. you applied an earlier version), make
the edit idempotent: add only what's missing, don't duplicate.

---

## 1. Create the project Dashboard

Create `20 Projects/Supplier Recon Tool/Supplier Recon Tool — Dashboard.md` with exactly
this content:

````markdown
# Supplier Recon Tool — Dashboard

> A deterministic tool that reconciles a client's supplier ledgers. It takes two Sage Business Cloud CSV exports (Supplier Transactions + Banks & Credit Cards), works out **why** supplier accounts don't reconcile, and produces worksheets the accountant actions **manually** in Sage. No AI matching, no Sage API, no writes to Sage.

**Status (2026-08-18): Built, tested (49 pytest), and pushed to GitHub (private). UI redesigned to a locked light "Apple" look with no sidebar. The one remaining step is the Streamlit Community Cloud deploy — a human, interactive login (see Deploy below).**

## Links

- Repo: `https://github.com/Elimperio1/supplier-recon-tool` (private, `main`)
- Local (work PC): `C:\Users\Elimp\Projects\Supplier-Recon-Tool`
- **Source of truth: `BUILD.md` in the repo** — the full implementation spec, every rule justified against the real exports, plus the §9 "golden numbers" the build must reproduce. This note links to it rather than duplicating client figures.
- Persistence: one Google Sheet "Supplier Recon - Memory" via a service account (infra + id in `BUILD.md` §7).

## Stack

Streamlit (UI only, `app.py`) · pure-stdlib recon engine in `recon/` (`parse`, `aliases`, `engine`, `match`) · gspread (Google Sheets memory) · openpyxl (Excel export) · Python 3.14. Deploy target: Streamlit Community Cloud. Tests: `pytest` — 49, including a golden-number regression that reproduces `BUILD.md` §9 against the real exports (skips automatically when the client CSVs are absent).

## What it produces

Six worksheets over one engine result (UI tabs plus an Excel download): **Summary** (every supplier, green/red) · **Payments Needed** (unmatched invoices, each with the bank Account Payment that likely settled it, tiered confident / ambiguous / none) · **Invoices Needed** · **Cross-Supplier** (duplicate or mis-captured accounts) · **Capture Typos** (same supplier, amounts within 5 cents) · **Integrity** (recomputed closing vs reported closing, to the cent).

## Hard constraints (do not violate)

- Pure deterministic code. **No AI/LLM matching, no Sage API, no writes to Sage.**
- Money is integer cents end-to-end; never float equality.
- Read BOTH the debit and credit columns for every balance — the column-sign trap (see [[Specs & Design]]).
- Dates are never a matching signal; never filter by "date <= today" (the closing date is deliberately in the future).

## Decisions & non-obvious facts

- **Client-data governance:** the two client CSV exports are gitignored (`*.csv` except `tests/fixtures/`) and have been removed from the machine — client financials never reach GitHub. The dev "auto-load sample CSVs" convenience was removed; the app now always requires uploads.
- **Seeded alias:** `USAVE` maps to Shoprite in the Sheet's `aliases` tab (global scope). Shoprite's confident bank match depends on it; the app reads aliases from the Sheet, not a hardcoded list.
- **UI:** locked light "Apple" aesthetic, **no Streamlit sidebar** (all inputs in the main column), all default chrome hidden, and **no emojis or en/em dashes** anywhere user-facing (the middot separator is kept). Theme in `.streamlit/config.toml` plus a CSS layer in `app.py`. See [[Web UI & Motion]] for the Streamlit-selector trap this hit.
- **Infra already exists** (GCP project + service account + shared Sheet) and is verified end-to-end; do not rebuild it — `BUILD.md` §7.
- **Git auth (work PC):** Git Credential Manager, so HTTPS `git push` works non-interactively. No `gh` CLI; the GitHub repo was created by hand.

## Deploy — the remaining human step

Code is on GitHub. To finish: create a Streamlit Community Cloud app pointing at `app.py`, and paste the local `.streamlit/secrets.toml` contents into the app's **Secrets** ( `[gcp_service_account]` + `[recon] spreadsheet_id` ) **before** the first deploy — env/secrets only reach a build set before it runs.

## Lessons touched

- [[Specs & Design]] — the accounting-export column-sign trap and recompute/reconcile discipline (why this tool's integrity check ships in the product, not as scaffolding).
- [[Web UI & Motion]] — a framework's generated DOM/class names are version-specific; target stable hooks and inspect the live DOM when a CSS override "doesn't take."

Back to [[Home]] · memory: [[Lessons]]
````

## 2. Link it from `Home.md`

Under the `## Active work` heading, add this line beneath the Practice Management entries:

```markdown
- [[Supplier Recon Tool — Dashboard]] — deterministic supplier reconciliation from Sage exports (built & on GitHub; Streamlit Cloud deploy pending)
```

## 3. Lesson — append to `03 Memory/Lessons/Web UI & Motion.md`

Bump its front-matter `updated:` to `2026-08-18`, and insert this section (immediately
before the `## \`prefers-*\` blocks` section):

````markdown
## ⚠️ A framework's generated DOM/class names are version-specific — target stable hooks, and inspect the LIVE DOM when an override "doesn't take"

**What happened (2026-08-18, Supplier-Recon-Tool):** the Streamlit app's tab bar was restyled into a segmented control with CSS targeting `[data-baseweb="tab-list"]` / `[data-baseweb="tab"]` — the selectors that work in older Streamlit. On Streamlit 1.61 those attributes no longer exist; the tab DOM had moved to `[role="tablist"]` and `[data-testid="stTab"]`. The rule matched nothing, so the segmented-control container simply never appeared. No error, valid CSS, and the *rest* of the same injected stylesheet (stat tiles, buttons, expanders) applied perfectly — so it read as "my CSS mostly works" rather than "these three selectors are dead."

**Why it's the silent-failure shape again:** a CSS rule whose selector matches zero elements is not an error. It looks identical to a rule that's being overridden by specificity, which looks identical to a rule that *is* working on an element you can't currently see. Only the live DOM tells the three apart.

**The rules:**
- **Never trust a framework's internal DOM structure across versions.** Streamlit, MUI/baseweb and friends rename generated classes and restructure their DOM between *minor* releases. `st-emotion-cache-*` (and any hashed class) is pure churn; `[data-baseweb=...]` is stable within a major version but not across.
- **Prefer the most durable hook available**, in order: `data-testid` (Streamlit's are semantic and survive longest) → ARIA roles (`[role="tablist"]`) → `data-baseweb` → hashed classes (never).
- **When a CSS override "doesn't take", inspect the live DOM *before* touching the CSS.** One element-tree dump or `getComputedStyle` tells you whether the selector matched at all — which distinguishes "wrong selector" from "lost specificity" from "right element, wrong property." Reaching for `!important` when the selector never matched is the biggest time-sink.
- **A partially-applied injected stylesheet is the trap.** When most of a `<style>` block works, the dead rules hide in plain sight. Verify each *distinct* selector family visually, not the sheet as a whole.
````

## 4. Lesson — append to `03 Memory/Lessons/Specs & Design.md`

Bump its front-matter `updated:` to `2026-08-18`, and add this bullet to the **Rules** list
of the existing lesson *"An accounting export moves the same fact between columns by sign"*
(right after the "recompute running totals ... reconcile" bullet):

````markdown
- **A structural detector keyed on a row's *shape* will also match sentinel and footer rows** (2026-08-18, same tool, at the full build). The section-header test was "col 0 non-empty, every other cell empty" — which the file's own `sep=,` first line and its `Grand Total:` footer both satisfy, minting a phantom section (a 4th "bank account" that was really the `sep=` line). Enumerate the known sentinel/footer lines and exclude them explicitly, and **assert the parsed section count against a figure you know independently** (here, the number of suppliers / bank accounts) so a phantom or a dropped section fails loudly instead of skewing every downstream total.
````

## 5. Trigger table — add rows to `03 Memory/Lessons.md`

Bump its front-matter `updated:` to `2026-08-18`, and add these two rows to the
`## 🚦 Trigger table` (put them just after the existing *"parse an accounting export"* row):

```markdown
| write a **structural detector** over an export (section headers, row types) | 🌐 [[Specs & Design]] | A shape-based test ("all other cells empty = section header") also matches sentinel/footer rows (`sep=,`, `Grand Total`) — exclude them explicitly and assert the section **count** against a total you know independently |
| **restyle a framework's generated UI** (Streamlit/MUI) with CSS | 🌐 [[Web UI & Motion]] | Generated class/DOM names are **version-specific** — target `data-testid`/ARIA roles, and inspect the **live DOM** when an override "doesn't take" (a selector matching zero elements is not an error) |
```

---

## Notes for the applying machine

- **No client data travels in this repo.** The two Sage CSV exports are gitignored; the
  §9 golden numbers live only in `BUILD.md`. Don't copy client figures into the vault.
- **Claude's machine-local auto-memory** (`~/.claude/projects/.../memory/`) does not sync
  and is per-machine. The vault Dashboard above is the shared, durable record — recreate
  local memory from it if you want, but it isn't required.
- After applying, this handoff file can stay in the repo as the record of what was synced.
