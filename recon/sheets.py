"""Google Sheets persistence layer (BUILD.md §5).

Kept free of Streamlit so it can be unit-tested; ``app.py`` injects the service
account dict and spreadsheet id (from ``st.secrets``) and owns the caching.

Two writers, both append-only and de-duplicated, both RAW so Sheets never coerces
a value into a date:
  * save a learned alias   -> ``aliases`` tab
  * log a confirmed match  -> ``match_log`` tab

Graceful degradation is the contract: every method fails soft. A dead memory
layer must never crash the recon - the caller warns and disables persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import gspread

ALIASES_TAB = "aliases"
MATCH_LOG_TAB = "match_log"

ALIAS_HEADERS = ["client", "supplier", "alias_pattern", "source", "notes"]
MATCH_LOG_HEADERS = [
    "logged_at", "client", "supplier", "amount", "bank_date", "bank_ref",
    "bank_desc", "current_allocation", "action", "status", "by",
]


class SheetsError(RuntimeError):
    """Raised on connection/setup failure so the caller can disable persistence."""


@dataclass
class AliasRow:
    client: str
    supplier: str
    alias_pattern: str
    source: str = "manual"
    notes: str = ""


@dataclass
class MatchLogEntry:
    client: str
    supplier: str
    amount: str            # rand string, RAW (never coerced)
    bank_date: str
    bank_ref: str
    bank_desc: str
    current_allocation: str
    action: str
    status: str = "confirmed"
    by: str = ""

    def as_row(self, logged_at: str) -> list[str]:
        return [
            logged_at, self.client, self.supplier, self.amount, self.bank_date,
            self.bank_ref, self.bank_desc, self.current_allocation, self.action,
            self.status, self.by,
        ]


def _norm(s: str) -> str:
    return (s or "").strip().lower()


class SheetsClient:
    """Thin wrapper over a single spreadsheet with the two recon tabs."""

    def __init__(self, spreadsheet):
        self._ss = spreadsheet

    # -- construction -------------------------------------------------------
    @classmethod
    def connect(cls, service_account_info: dict, spreadsheet_id: str) -> "SheetsClient":
        try:
            gc = gspread.service_account_from_dict(service_account_info)
            ss = gc.open_by_key(spreadsheet_id)
        except Exception as exc:  # noqa: BLE001 - fail soft to the caller
            raise SheetsError(f"Google Sheets unreachable: {exc}") from exc
        return cls(ss)

    def _worksheet(self, title: str, headers: list[str]):
        try:
            return self._ss.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self._ss.add_worksheet(title=title, rows=1000, cols=max(10, len(headers)))
            ws.append_row(headers, value_input_option="RAW")
            return ws

    # -- reads --------------------------------------------------------------
    def read_aliases(self) -> list[AliasRow]:
        ws = self._worksheet(ALIASES_TAB, ALIAS_HEADERS)
        values = ws.get_all_values()
        if not values:
            return []
        header = [h.strip().lower() for h in values[0]]
        idx = {h: i for i, h in enumerate(header)}
        out: list[AliasRow] = []
        for row in values[1:]:
            def get(col: str) -> str:
                i = idx.get(col)
                return row[i].strip() if i is not None and i < len(row) else ""
            if not get("supplier") and not get("alias_pattern"):
                continue
            out.append(AliasRow(
                client=get("client"), supplier=get("supplier"),
                alias_pattern=get("alias_pattern"), source=get("source"),
                notes=get("notes"),
            ))
        return out

    # -- writes (append-only, deduped, RAW) ---------------------------------
    def append_alias(self, row: AliasRow) -> bool:
        """Append unless (client, supplier, alias_pattern) already exists."""
        key = (_norm(row.client), _norm(row.supplier), _norm(row.alias_pattern))
        for existing in self.read_aliases():
            if (_norm(existing.client), _norm(existing.supplier),
                    _norm(existing.alias_pattern)) == key:
                return False
        ws = self._worksheet(ALIASES_TAB, ALIAS_HEADERS)
        ws.append_row(
            [row.client, row.supplier, row.alias_pattern, row.source, row.notes],
            value_input_option="RAW",
        )
        return True

    def append_match_log(self, entry: MatchLogEntry) -> bool:
        """Append unless (client, supplier, amount, bank_ref) already logged."""
        ws = self._worksheet(MATCH_LOG_TAB, MATCH_LOG_HEADERS)
        values = ws.get_all_values()
        if values:
            header = [h.strip().lower() for h in values[0]]
            idx = {h: i for i, h in enumerate(header)}

            def cell(row, col):
                i = idx.get(col)
                return row[i].strip() if i is not None and i < len(row) else ""

            key = (_norm(entry.client), _norm(entry.supplier),
                   _norm(entry.amount), _norm(entry.bank_ref))
            for row in values[1:]:
                if (_norm(cell(row, "client")), _norm(cell(row, "supplier")),
                        _norm(cell(row, "amount")), _norm(cell(row, "bank_ref"))) == key:
                    return False
        logged_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ws.append_row(entry.as_row(logged_at), value_input_option="RAW")
        return True
