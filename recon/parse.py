"""Parse the two Sage Business Cloud CSV exports into plain dataclasses.

Everything here is stdlib only (``csv`` + ``decimal``). See BUILD.md §2 - every
rule below has a real failure on the real data behind it.

Money is integer **cents** end-to-end. Sign conventions (BUILD.md §2.1):
  * Supplier ledger:  ``signed = credit - debit``  (credit-positive)
  * Bank report:      ``signed = debit  - credit``  (inverted semantics, §2.4)

Balances (opening AND closing) live in whichever of the Debit/Credit columns
matches their sign, so we ALWAYS read both columns and subtract - never one.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Union

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

Source = Union[str, bytes, bytearray, os.PathLike]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def to_cents(raw: str) -> Optional[int]:
    """Parse a Sage money cell to integer cents. Empty/blank -> None.

    Handles thousands separators, an optional leading ``R``, a leading ``-`` and
    parenthesised negatives ``(123.45)``. Uses Decimal (never float) so cent-level
    capture typos in the source stay exact.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()
    s = s.replace(" ", "").replace(",", "")  # thousands separators / stray spaces
    if s[:1] in ("R", "r"):
        s = s[1:]
    if s.startswith("-"):
        neg = True
        s = s[1:]
    if s.startswith("+"):
        s = s[1:]
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    cents = int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -cents if neg else cents


def read_csv_rows(source: Source) -> list[list[str]]:
    """Return CSV rows from a path or raw bytes, decoded as utf-8-sig (BOM).

    Uses ``csv.reader`` so the multiline header cell and quoted commas parse
    correctly. NEVER split on newlines by hand (BUILD.md §2).
    """
    if isinstance(source, (bytes, bytearray)):
        text = bytes(source).decode("utf-8-sig")
    else:
        with open(source, "r", encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
    return list(csv.reader(io.StringIO(text)))


def _is_sep_line(row: list[str]) -> bool:
    return bool(row) and row[0].strip().lower().startswith("sep=")


def _is_column_header(row: list[str]) -> bool:
    # The one true header row carries these labels; identify it robustly rather
    # than trusting its position, so we never parse it as data or a section.
    joined = "\x00".join(c.strip() for c in row)
    return "Transaction Type" in joined and "Reference" in joined


def _is_section_header(row: list[str]) -> bool:
    """Section header = col0 non-empty and *every* other cell empty (§2)."""
    if not row or not row[0].strip():
        return False
    return all(c.strip() == "" for c in row[1:])


def _cell(row: list[str], idx: int) -> str:
    return row[idx] if idx < len(row) else ""


# ---------------------------------------------------------------------------
# Supplier report
# ---------------------------------------------------------------------------
# Columns: Date(0) Reference(1) TransactionType(2) Description(3) Debit(4) Credit(5) Balance(6)
S_DEBIT, S_CREDIT, S_BALANCE = 4, 5, 6


@dataclass
class SupplierTxn:
    supplier: str
    date: str            # raw DD/MM/YYYY
    reference: str
    txn_type: str
    description: str
    debit: Optional[int]   # cents
    credit: Optional[int]  # cents
    balance_raw: str
    row_index: int         # global CSV row index (identity / display)

    @property
    def signed(self) -> int:
        return (self.credit or 0) - (self.debit or 0)


@dataclass
class Supplier:
    name: str
    opening: int                 # signed cents
    closing: int                 # signed cents (reported)
    txns: list[SupplierTxn] = field(default_factory=list)
    recomputed_closing: int = 0  # opening + Σ signed(txn)
    duplicate: bool = False      # name seen more than once

    @property
    def integrity_ok(self) -> bool:
        return self.recomputed_closing == self.closing

    @property
    def integrity_delta(self) -> int:
        return self.recomputed_closing - self.closing


@dataclass
class SupplierReport:
    suppliers: list[Supplier] = field(default_factory=list)
    unrecognized: list[tuple[int, list[str]]] = field(default_factory=list)
    duplicate_names: list[str] = field(default_factory=list)

    @property
    def integrity_failures(self) -> list[Supplier]:
        return [s for s in self.suppliers if not s.integrity_ok]


def _balance_signed(row: list[str], debit_idx: int, credit_idx: int, credit_positive: bool) -> int:
    debit = to_cents(_cell(row, debit_idx)) or 0
    credit = to_cents(_cell(row, credit_idx)) or 0
    return (credit - debit) if credit_positive else (debit - credit)


def parse_supplier_report(source: Source) -> SupplierReport:
    rows = read_csv_rows(source)
    report = SupplierReport()
    seen_names: dict[str, int] = {}
    current: Optional[Supplier] = None

    for i, row in enumerate(rows):
        if _is_sep_line(row) or _is_column_header(row):
            continue
        c0 = (row[0].strip() if row else "")

        if _is_section_header(row):
            current = Supplier(name=c0, opening=0, closing=0)
            seen_names[c0] = seen_names.get(c0, 0) + 1
            report.suppliers.append(current)
            continue

        if current is None:
            # Data before any section header - should not happen; never drop it.
            if c0:
                report.unrecognized.append((i, row))
            continue

        if c0.startswith("Opening Balance"):
            current.opening = _balance_signed(row, S_DEBIT, S_CREDIT, credit_positive=True)
        elif c0.startswith("Closing Balance"):
            current.closing = _balance_signed(row, S_DEBIT, S_CREDIT, credit_positive=True)
        elif c0.startswith("Movement for the period") or c0.startswith("Grand Total"):
            continue  # known footers; carry a value cell - never a txn/header
        elif DATE_RE.match(c0):
            current.txns.append(SupplierTxn(
                supplier=current.name,
                date=c0,
                reference=_cell(row, 1).strip(),
                txn_type=_cell(row, 2).strip(),
                description=_cell(row, 3),
                debit=to_cents(_cell(row, S_DEBIT)),
                credit=to_cents(_cell(row, S_CREDIT)),
                balance_raw=_cell(row, S_BALANCE).strip(),
                row_index=i,
            ))
        elif c0:
            report.unrecognized.append((i, row))

    for s in report.suppliers:
        s.recomputed_closing = s.opening + sum(t.signed for t in s.txns)
        s.duplicate = seen_names.get(s.name, 0) > 1

    report.duplicate_names = sorted(n for n, c in seen_names.items() if c > 1)
    return report


# ---------------------------------------------------------------------------
# Bank report
# ---------------------------------------------------------------------------
# Columns: BankAccount(0) Payee(1) Description(2) Reference(3) TransactionType(4)
#          Account/Customer/Supplier(5) Debit(6) Credit(7) Balance(8)
# Inverted semantics (§2.4): money OUT = Credit column, money IN = Debit column.
B_DESC, B_REF, B_TYPE, B_ACCT, B_DEBIT, B_CREDIT, B_BALANCE = 2, 3, 4, 5, 6, 7, 8


@dataclass
class BankTxn:
    account: str            # bank account section name
    row_index: int          # global CSV row index - identity (§2.4: never the ref alone)
    date: str
    payee: str
    description: str
    reference: str          # batch ref, may repeat across rows
    txn_type: str
    allocation: str         # "Account / Customer / Supplier" - GL acct or supplier/customer
    debit: Optional[int]    # cents (money IN)
    credit: Optional[int]   # cents (money OUT)
    balance_raw: str

    @property
    def amount_out(self) -> Optional[int]:
        """Money out (payment) = the Credit column (inverted semantics)."""
        return self.credit

    @property
    def signed(self) -> int:
        return (self.debit or 0) - (self.credit or 0)


@dataclass
class BankAccount:
    name: str
    opening: int
    closing: int
    txns: list[BankTxn] = field(default_factory=list)
    recomputed_closing: int = 0

    @property
    def integrity_ok(self) -> bool:
        return self.recomputed_closing == self.closing

    @property
    def integrity_delta(self) -> int:
        return self.recomputed_closing - self.closing


@dataclass
class BankReport:
    accounts: list[BankAccount] = field(default_factory=list)
    unrecognized: list[tuple[int, list[str]]] = field(default_factory=list)

    @property
    def all_txns(self) -> list[BankTxn]:
        return [t for a in self.accounts for t in a.txns]

    @property
    def integrity_failures(self) -> list[BankAccount]:
        return [a for a in self.accounts if not a.integrity_ok]


def parse_bank_report(source: Source) -> BankReport:
    rows = read_csv_rows(source)
    report = BankReport()
    current: Optional[BankAccount] = None

    for i, row in enumerate(rows):
        if _is_sep_line(row) or _is_column_header(row):
            continue
        c0 = (row[0].strip() if row else "")

        if _is_section_header(row):
            current = BankAccount(name=c0, opening=0, closing=0)
            report.accounts.append(current)
            continue

        if current is None:
            if c0:
                report.unrecognized.append((i, row))
            continue

        if c0.startswith("Opening Balance"):
            current.opening = _balance_signed(row, B_DEBIT, B_CREDIT, credit_positive=False)
        elif c0.startswith("Closing Balance"):
            current.closing = _balance_signed(row, B_DEBIT, B_CREDIT, credit_positive=False)
        elif c0.startswith("Movement for the period") or c0.startswith("Grand Total"):
            continue
        elif DATE_RE.match(c0):
            current.txns.append(BankTxn(
                account=current.name,
                row_index=i,
                date=c0,
                payee=_cell(row, 1).strip(),
                description=_cell(row, B_DESC),
                reference=_cell(row, B_REF).strip(),
                txn_type=_cell(row, B_TYPE).strip(),
                allocation=_cell(row, B_ACCT).strip(),
                debit=to_cents(_cell(row, B_DEBIT)),
                credit=to_cents(_cell(row, B_CREDIT)),
                balance_raw=_cell(row, B_BALANCE).strip(),
            ))
        elif c0:
            report.unrecognized.append((i, row))

    for a in report.accounts:
        a.recomputed_closing = a.opening + sum(t.signed for t in a.txns)
    return report
