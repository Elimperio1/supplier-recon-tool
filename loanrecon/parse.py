"""Read a Sage or Pastel ledger CSV into signed transactions.

Every dated row is kept for the workbook's copy of the ledger. Each one becomes a
transaction whose amount is debit minus credit in cents, so a negative figure in
the Debit column (Sage moves a value between columns by sign) still lands on the
right side. Rows that carry an amount but cannot be read are reported, never
dropped silently.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

FORMATS = {
    "Sage": "Account Date",
    "Pastel": "Date",
}

DATE_FORMATS = ("%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%d", "%d-%m-%Y")


class ParseError(Exception):
    """The file cannot be reconciled at all. The message is shown to the user."""


@dataclass(frozen=True)
class Txn:
    row: int            # index into Ledger.rows
    date: date
    description: str
    cents: int          # debit minus credit; positive is a debit

    @property
    def debit(self) -> int:
        return self.cents if self.cents > 0 else 0

    @property
    def credit(self) -> int:
        return -self.cents if self.cents < 0 else 0


@dataclass
class Ledger:
    name: str
    headers: list[str]
    rows: list[list[str]]                 # dated rows, as exported
    txns: list[Txn]                       # rows with a non-zero amount
    warnings: list[str] = field(default_factory=list)

    @property
    def net(self) -> int:
        return sum(t.cents for t in self.txns)

    @property
    def debits(self) -> int:
        return sum(t.debit for t in self.txns)

    @property
    def credits(self) -> int:
        return sum(t.credit for t in self.txns)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_date(text: str) -> date | None:
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_AMOUNT_JUNK = re.compile(r"[R\s ,]")


def parse_cents(text: str) -> int:
    """'1,234.50' -> 123450, '(500.00)' -> -50000, '' -> 0. Raises ValueError if unreadable."""
    s = _AMOUNT_JUNK.sub("", text.strip())
    if not s or s == "-":
        return 0
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.endswith("-"):                      # trailing minus, as some exports write it
        negative, s = not negative, s[:-1]
    if s.startswith("-"):
        negative, s = not negative, s[1:]
    if not re.fullmatch(r"\d+(\.\d{1,2})?|\.\d{1,2}", s):
        raise ValueError(text)
    whole, _, frac = s.partition(".")
    cents = int(whole or 0) * 100 + int((frac + "00")[:2])
    return -cents if negative else cents


def _find(headers: list[str], wanted: str) -> int | None:
    key = wanted.strip().lower()
    for i, h in enumerate(headers):
        if h.strip().lower() == key:
            return i
    return None


def parse_ledger(data: bytes, fmt: str, name: str) -> Ledger:
    date_col = FORMATS[fmt]
    rows = list(csv.reader(io.StringIO(_decode(data))))

    header_idx = next((i for i, r in enumerate(rows) if _find(r, date_col) is not None), None)
    if header_idx is None:
        other = [f for f, col in FORMATS.items()
                 if f != fmt and any(_find(r, col) is not None for r in rows[:50])]
        hint = f" It looks like a {other[0]} export; change the format." if other else ""
        raise ParseError(f"No '{date_col}' column found for a {fmt} export.{hint}")

    headers = [h.strip() for h in rows[header_idx]]
    cols = {key: _find(headers, key) for key in (date_col, "Description", "Debit", "Credit")}
    missing = [key for key, idx in cols.items() if idx is None]
    if missing:
        raise ParseError(
            f"Missing column {', '.join(repr(m) for m in missing)}. "
            f"Columns found: {', '.join(h for h in headers if h)}."
        )

    kept: list[list[str]] = []
    txns: list[Txn] = []
    bad_dates = bad_amounts = 0

    for raw in rows[header_idx + 1:]:
        row = raw + [""] * (len(headers) - len(raw))
        date_text = row[cols[date_col]].strip()
        if not date_text:
            continue                                   # blank, subtotal or footer row
        when = parse_date(date_text)
        try:
            cents = parse_cents(row[cols["Debit"]]) - parse_cents(row[cols["Credit"]])
        except ValueError:
            if when is not None:
                bad_amounts += 1
            continue
        if when is None:
            if cents:
                bad_dates += 1
            continue
        kept.append(row)
        if cents:
            txns.append(Txn(len(kept) - 1, when, row[cols["Description"]].strip(), cents))

    if not txns:
        raise ParseError("No transactions found under the header row.")

    warnings = []
    if bad_dates:
        warnings.append(f"{bad_dates} row(s) with an amount but an unreadable date were left out. "
                        "Check the export's date format.")
    if bad_amounts:
        warnings.append(f"{bad_amounts} dated row(s) with an unreadable Debit or Credit were left out.")
    return Ledger(name, headers, kept, txns, warnings)
