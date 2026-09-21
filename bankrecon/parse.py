"""Parse the two CSV inputs into transactions with integer-cent amounts.

Both files go through ``csv.reader``; nothing is split by hand. The header row is
found by scanning for a row that carries a Date column and an amount column
(``Amount`` or ``Total``), so a ``#META`` block from the CSV Parser or a report
title above the header is skipped and counted, never mistaken for data. Below
the header, a row whose date or amount will not parse is recorded in
``Parsed.skipped`` with its row number and a reason. Nothing is dropped silently.

Money is integer cents end to end. Floats never enter the engine.
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

DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y")

# Column names accepted for each field, lower-cased. First hit wins.
COLUMN_ALIASES = {
    "date": ("date", "account date", "transaction date"),
    "description": ("description", "details", "narrative"),
    "amount": ("amount", "total"),
    "comment": ("comment", "comments"),
    "reference": ("reference", "ref"),
    "txn_type": ("type",),
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
    comment: str = ""        # Sage: the bank narrative when Description was renamed
    reference: str = ""
    txn_type: str = ""
    selection: str = ""
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

    @property
    def first_date(self) -> Optional[date]:
        return min((t.date for t in self.txns), default=None)

    @property
    def last_date(self) -> Optional[date]:
        return max((t.date for t in self.txns), default=None)


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


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if not cells or all(c == "" for c in cells):
            continue
        if any(c.startswith("#") for c in cells[:1]):
            continue
        has_date = any(c in COLUMN_ALIASES["date"] for c in cells)
        has_amount = any(c in COLUMN_ALIASES["amount"] for c in cells)
        if has_date and has_amount:
            cols: dict[str, int] = {}
            for field_name, names in COLUMN_ALIASES.items():
                for name in names:
                    if name in cells:
                        cols[field_name] = cells.index(name)
                        break
            return i, cols
    raise ParseError("No header row with a Date column and an Amount or Total column was found.")


def parse_file(data: bytes, side: str) -> Parsed:
    text = decode(data)
    rows = list(csv.reader(io.StringIO(text)))
    header_idx, cols = _find_header(rows)
    parsed = Parsed(side=side, preamble_rows=header_idx,
                    header=[c.strip() for c in rows[header_idx]])
    amount_col = cols["amount"]
    parsed.amount_column = parsed.header[amount_col]
    desc_col = cols.get("description")

    def cell(row: list[str], name: str) -> str:
        idx = cols.get(name)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    for offset, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not row or all(c.strip() == "" for c in row):
            continue
        if row[0].strip().startswith("#"):
            parsed.skipped.append((offset, "comment line"))
            continue
        raw_date = cell(row, "date")
        when = parse_date(raw_date)
        if when is None:
            parsed.skipped.append((offset, f"date not recognised: '{raw_date}'"))
            continue
        raw_amount = row[amount_col].strip() if amount_col < len(row) else ""
        cents = parse_cents(raw_amount)
        if cents is None:
            parsed.skipped.append((offset, f"amount not recognised: '{raw_amount}'"))
            continue
        description = row[desc_col].strip() if desc_col is not None and desc_col < len(row) else ""
        parsed.txns.append(Txn(
            side=side, row=offset, date=when, cents=cents, description=description,
            comment=cell(row, "comment"), reference=cell(row, "reference"),
            txn_type=cell(row, "txn_type"), selection=cell(row, "selection"),
            reconciled=cell(row, "reconciled"),
        ))
    return parsed
