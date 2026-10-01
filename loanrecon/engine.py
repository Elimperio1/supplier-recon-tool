"""Match two sides of an intercompany loan.

A debit in company A pairs with a credit of the same amount in company B. Pass 1
pairs entries on the same date (confirmed). Pass 2 pairs what is left within the
date tolerance, nearest date first (to review). The rest is unmatched.

The balances reconcile when A's net plus B's net is zero: every matched pair
cancels, so any difference is exactly the sum of the unmatched entries.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .parse import Ledger, Txn

CONFIRMED = "confirmed"
REVIEW = "review"
UNMATCHED = "unmatched"


@dataclass(frozen=True)
class Match:
    a: Txn
    b: Txn
    tier: str

    @property
    def days(self) -> int:
        return abs((self.a.date - self.b.date).days)


@dataclass
class Result:
    a: Ledger
    b: Ledger
    tolerance: int
    matches: list[Match]
    unmatched_a: list[Txn]
    unmatched_b: list[Txn]

    @property
    def confirmed(self) -> list[Match]:
        return [m for m in self.matches if m.tier == CONFIRMED]

    @property
    def review(self) -> list[Match]:
        return [m for m in self.matches if m.tier == REVIEW]

    @property
    def difference(self) -> int:
        return self.a.net + self.b.net

    def status_a(self) -> dict[int, str]:
        return {m.a.row: m.tier for m in self.matches}

    def status_b(self) -> dict[int, str]:
        return {m.b.row: m.tier for m in self.matches}


def reconcile(a: Ledger, b: Ledger, tolerance: int = 2) -> Result:
    # B's entries by amount, oldest first, so equal candidates resolve by date.
    pool: dict[int, list[Txn]] = defaultdict(list)
    for t in sorted(b.txns, key=lambda t: (t.date, t.row)):
        pool[t.cents].append(t)

    used_a: set[int] = set()
    used_b: set[int] = set()
    matches: list[Match] = []

    for tier, window in ((CONFIRMED, 0), (REVIEW, tolerance)):
        for ta in a.txns:
            if ta.row in used_a:
                continue
            best = None
            for tb in pool.get(-ta.cents, ()):
                if tb.row in used_b:
                    continue
                days = abs((ta.date - tb.date).days)
                if days <= window and (best is None or days < best[0]):
                    best = (days, tb)
            if best:
                used_a.add(ta.row)
                used_b.add(best[1].row)
                matches.append(Match(ta, best[1], tier))

    return Result(
        a=a, b=b, tolerance=tolerance, matches=matches,
        unmatched_a=[t for t in a.txns if t.row not in used_a],
        unmatched_b=[t for t in b.txns if t.row not in used_b],
    )


def rand(cents: int) -> str:
    """House format: R, non-breaking-space thousands, minus sign for negatives."""
    text = f"{abs(cents) / 100:,.2f}".replace(",", " ")
    return f"-R {text}" if cents < 0 else f"R {text}"
