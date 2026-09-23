"""Parse the CSV inputs into transactions with integer-cent amounts.

The bank CSV from the CSV Parser is Date, Description (or Details), Amount, with
an optional ``#META`` block above the header.

Two Sage layouts are accepted on the Sage side, told apart by the header row:

* Bank Transactions export: one row per line with Date, Description, Total
  (signed), plus Reference, Type, Selection, Comment and Reconciled.
* Banks and Credit Cards Transactions Report: a ``sep=,`` line, a header whose
  first cell reads "Bank Account" over "Date" on two lines, then one section
  per bank account (a row holding only the account name), an Opening Balance
  row, dated rows with Debit (money in) and Credit (money out), a Closing
  Balance row, and Movement and Grand Total rows. Money is Debit minus Credit.
  On Supplier Payment and Customer Receipt rows the Description holds the
  batch reference and the Account column holds the name, so the batch
  reference is what groups split allocations and the name is kept as the
  comment. The report can hold several accounts; one is chosen.

Everything goes through ``csv.reader``; nothing is split by hand. Rows below
the header whose date or amount will not parse are recorded in
``Parsed.skipped`` with a row number and a reason. Nothing is dropped silently.
Money is integer cents end to end; floats never enter the engine.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

SIDE_CSV = "csv"
SIDE_SAGE = "sage"

FORMAT_SIMPLE = "simple"          # one amount column
FORMAT_SECTIONED = "sectioned"    # Banks and Credit Cards report: sections, Debit and Credit

DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y")
BATCH_REF = re.compile(r"^\d{8}-\d{4}$")

# Column names accepted for each field, lower-cased. First hit wins.
COLUMN_ALIASES = {
    "date": ("date", "account date", "transaction date"),
    "description": ("description", "details", "narrative"),
    "amount": ("amount", "total"),
    "debit": ("debit",),
    "credit": ("credit",),
    "comment": ("comment", "comments", "account / customer / supplier"),
    "reference": ("reference", "ref"),
    "txn_type": ("type", "transaction type"),
    "selection": ("selection", "account"),
    "reconciled": ("reconciled",),
}


@dataclass(frozen=True)
class Txn:
    side: str
    row: int                 # 1-based line number in the source file
    date: date
    cents: int
    description: str
    comment: str = ""        # Sage: the bank narrative, or the supplier/customer name
    reference: str = ""
    txn_type: str = ""
    selection: str = ""      # Sage: the Selection column, or the bank account of the section
    reconciled: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return (self.side, self.row)


@dataclass
class Parsed:
    side: str
    txns: list[Txn] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)   # (row, reason)
    preamble_rows: int = 0
    header: list[str] = field(default_factory=list)
    amount_column: str = ""
    format: str = FORMAT_SIMPLE
    accounts: list[str] = field(default_factory=list)   # sectioned: every account in the file
    account: str = ""                                    # sectioned: the account these txns belong to
    opening: Optional[int] = None                        # sectioned: the account's Opening Balance
    closing: Optional[int] = None                        # sectioned: the account's Closing Balance

    @property
    def first_date(self) -> Optional[date]:
        return min((t.date for t in self.txns), default=None)

    @property
    def last_date(self) -> Optional[date]:
        return max((t.date for t in self.txns), default=None)

    @property
    def movement(self) -> int:
        return sum(t.cents for t in self.txns)

    @property
    def integrity_ok(self) -> Optional[bool]:
        """Opening plus every line equals the reported closing, to the cent. None when the file has no balances."""
        if self.opening is None or self.closing is None:
            return None
        return self.opening + self.movement == self.closing


class ParseError(ValueError):
    """The file has no usable header row."""


def decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_date(text: str) -> Optional[date]:
    s = (text or "").strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+$")


def parse_cents(text: str) -> Optional[int]:
    """'-1,234.56' -> -123456. Accepts (12.00), 12.00-, R 5.00, 1 234,56, 4-decimal Sage totals."""
    s = (text or "").strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    if s.endswith("-"):
        negative = True
        s = s[:-1]
    s = re.sub(r"[^0-9,.\-]", "", s)          # drops currency letters and spaces
    if s.startswith("-"):
        negative = True
        s = s[1:]
    if not s:
        return None
    if "," in s and "." not in s and not _THOUSANDS_COMMA.match(s):
        s = s.replace(",", ".")                # decimal comma: 1234,56
    else:
        s = s.replace(",", "")                 # thousands comma: 1,234.56
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -cents if negative else cents


def header_name(cell: str) -> str:
    """'Bank Account \\n   Date' -> 'date'; 'Account / Customer / Supplier' -> lower-cased, one space."""
    lines = [re.sub(r"\s+", " ", line).strip().lower() for line in (cell or "").splitlines()]
    lines = [line for line in lines if line]
    return lines[-1] if lines else ""


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int], str]:
    for i, row in enumerate(rows):
        cells = [header_name(c) for c in row]
        if not cells or all(c == "" for c in cells):
            continue
        if cells[0].startswith("#") or cells[0].startswith("sep="):
            continue
        has_date = any(c in COLUMN_ALIASES["date"] for c in cells)
        has_amount = any(c in COLUMN_ALIASES["amount"] for c in cells)
        has_debit_credit = "debit" in cells and "credit" in cells
        if has_date and (has_amount or has_debit_credit):
            cols: dict[str, int] = {}
            for field_name, names in COLUMN_ALIASES.items():
                for name in names:
                    if name in cells:
                        cols[field_name] = cells.index(name)
                        break
            fmt = FORMAT_SECTIONED if has_debit_credit and not has_amount else FORMAT_SIMPLE
            return i, cols, fmt
    raise ParseError("No header row with a Date column and an Amount, Total, or Debit and Credit "
                     "columns was found.")


def _cell(row: list[str], cols: dict[str, int], name: str) -> str:
    idx = cols.get(name)
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _blank(row: list[str]) -> bool:
    return not row or all(c.strip() == "" for c in row)


def parse_file(data: bytes, side: str, account: Optional[str] = None) -> Parsed:
    """Parse one uploaded file. ``account`` picks a section of a sectioned report; default the first."""
    text = decode(data)
    rows = list(csv.reader(io.StringIO(text)))
    header_idx, cols, fmt = _find_header(rows)
    header = [re.sub(r"\s+", " ", c).strip() for c in rows[header_idx]]
    if fmt == FORMAT_SECTIONED:
        return _parse_sectioned(rows, header_idx, cols, side, account, header)
    return _parse_simple(rows, header_idx, cols, side, header)


def _parse_simple(rows, header_idx, cols, side, header) -> Parsed:
    parsed = Parsed(side=side, preamble_rows=header_idx, header=header, format=FORMAT_SIMPLE)
    amount_col = cols["amount"]
    parsed.amount_column = rows[header_idx][amount_col].strip()

    for offset, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if _blank(row):
            continue
        if row[0].strip().startswith("#"):
            parsed.skipped.append((offset, "comment line"))
            continue
        raw_date = _cell(row, cols, "date")
        when = parse_date(raw_date)
        if when is None:
            parsed.skipped.append((offset, f"date not recognised: '{raw_date}'"))
            continue
        raw_amount = row[amount_col].strip() if amount_col < len(row) else ""
        cents = parse_cents(raw_amount)
        if cents is None:
            parsed.skipped.append((offset, f"amount not recognised: '{raw_amount}'"))
            continue
        parsed.txns.append(Txn(
            side=side, row=offset, date=when, cents=cents,
            description=_cell(row, cols, "description"),
            comment=_cell(row, cols, "comment"), reference=_cell(row, cols, "reference"),
            txn_type=_cell(row, cols, "txn_type"), selection=_cell(row, cols, "selection"),
            reconciled=_cell(row, cols, "reconciled"),
        ))
    return parsed


def _debit_minus_credit(row: list[str], cols: dict[str, int]) -> Optional[int]:
    debit = parse_cents(_cell(row, cols, "debit"))
    credit = parse_cents(_cell(row, cols, "credit"))
    if debit is None and credit is None:
        return None
    return (debit or 0) - (credit or 0)


def _parse_sectioned(rows, header_idx, cols, side, account, header) -> Parsed:
    sections: dict[str, dict] = {}
    order: list[str] = []
    skipped: list[tuple[int, str]] = []
    current: Optional[dict] = None
    current_name = ""

    for offset, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if _blank(row):
            continue
        c0 = row[0].strip()
        when = parse_date(c0)
        if when is None:
            low = c0.lower()
            if low.startswith("opening balance"):
                if current is not None:
                    current["opening"] = _debit_minus_credit(row, cols)
            elif low.startswith("closing balance"):
                if current is not None:
                    current["closing"] = _debit_minus_credit(row, cols)
            elif low.startswith("movement for the period") or low.startswith("grand total"):
                continue
            elif c0 and all(c.strip() == "" for c in row[1:]):
                current_name = c0
                if c0 not in sections:
                    sections[c0] = {"txns": [], "opening": None, "closing": None}
                    order.append(c0)
                current = sections[c0]
            else:
                skipped.append((offset, f"row not recognised: '{c0[:40]}'"))
            continue
        if current is None:
            skipped.append((offset, "dated row above the first bank account header"))
            continue
        cents = _debit_minus_credit(row, cols)
        if cents is None:
            skipped.append((offset, "no debit or credit amount"))
            continue
        description = _cell(row, cols, "description")
        reference = description if BATCH_REF.match(description) else _cell(row, cols, "reference")
        current["txns"].append(Txn(
            side=side, row=offset, date=when, cents=cents, description=description,
            comment=_cell(row, cols, "comment"), reference=reference,
            txn_type=_cell(row, cols, "txn_type"), selection=current_name,
        ))

    if not sections:
        raise ParseError("No bank account section was found below the header.")
    chosen = account if account in sections else order[0]
    section = sections[chosen]
    return Parsed(side=side, txns=section["txns"], skipped=skipped, preamble_rows=header_idx,
                  header=header, amount_column="Debit minus Credit", format=FORMAT_SECTIONED,
                  accounts=order, account=chosen,
                  opening=section["opening"], closing=section["closing"])
