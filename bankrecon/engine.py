"""Reconcile the bank CSV (source of truth) against the Sage bank export.

Window. Only the dates both files cover are compared: from the later of the two
first dates to the earlier of the two last dates. Rows outside that window are
kept and reported as "Outside window", never dropped.

Matching is one to one on a multiset, so three R155 fees on one side against two
on the other leave exactly one unmatched. Passes run in this order and a line is
consumed by the first pass that claims it:

1. Exact: same date, same cents, same normalised description (the CSV text
   against Sage's Description or its Comment, which holds the bank narrative
   when the Description was renamed to a supplier name).
2. Same date, same cents, descriptions differ. With name evidence, a
   distinctive word shared with Sage's description or account name, or one
   text found inside the other, the pair is "Name" and counts as matched
   (Sage's Banks and Credit Cards report names the supplier instead of
   quoting the bank narrative). Without evidence the pair is "Amount only"
   and is reported for review. Best evidence wins, then most words in
   common, then file order.
3. Split: one CSV line equals the sum of two or more Sage lines on the same
   date that share a Reference (Sage splits one bank line into allocations
   under one reference). Sage lines without a reference are never grouped.
   The mirror case, one Sage line equal to the sum of several CSV lines with
   the same description and the same sign on the same date, is also claimed.
   Reported for review.
4. Date differs: only when a tolerance in days is given. Same cents and same
   normalised description within the tolerance; nearest date wins. Reported
   for review.

What is left on the CSV side is "Missing in Sage"; what is left on the Sage
side is "Extra in Sage". The engine proves itself on every run: missing total
minus extra total must equal CSV total minus Sage total, to the cent.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .parse import Parsed, Txn

ENGINE_VERSION = 2

TIER_EXACT = "Exact"
TIER_NAME = "Name"
TIER_AMOUNT = "Amount only"
TIER_SPLIT = "Split"
TIER_DATE = "Date differs"
REVIEW_TIERS = (TIER_AMOUNT, TIER_SPLIT, TIER_DATE)

STATUS_MATCHED = "Matched"
STATUS_NAME = "Matched, name"
STATUS_AMOUNT = "Matched, amount only"
STATUS_SPLIT = "Matched, split"
STATUS_DATE = "Matched, date differs"
STATUS_MISSING = "Missing in Sage"
STATUS_EXTRA = "Extra in Sage"
STATUS_OUTSIDE = "Outside window"

TIER_STATUS = {
    TIER_EXACT: STATUS_MATCHED,
    TIER_NAME: STATUS_NAME,
    TIER_AMOUNT: STATUS_AMOUNT,
    TIER_SPLIT: STATUS_SPLIT,
    TIER_DATE: STATUS_DATE,
}

# Words that carry no identity: they appear on most bank lines and match nothing in
# particular. A pair whose only shared word is one of these rests on amount alone.
STOPWORDS = frozenset({
    "payment", "payments", "purchase", "purchases", "transfer", "debit", "credit",
    "account", "fees", "card", "magtape", "electronic", "banking", "service",
    "agreement", "insurance", "premium", "cash", "bank", "charges", "transaction",
    "deposit", "receipt", "from", "with", "online", "immediate", "monthly", "internet",
    "order", "cheque", "withdrawal", "loan", "repayment", "confirm", "email",
})


@dataclass(frozen=True)
class Window:
    start: date
    end: date

    def contains(self, when: date) -> bool:
        return self.start <= when <= self.end


@dataclass
class Match:
    tier: str
    csv_txns: list[Txn]
    sage_txns: list[Txn]
    note: str = ""

    @property
    def csv_cents(self) -> int:
        return sum(t.cents for t in self.csv_txns)

    @property
    def sage_cents(self) -> int:
        return sum(t.cents for t in self.sage_txns)

    @property
    def date(self) -> date:
        return min(t.date for t in self.csv_txns)


@dataclass
class Result:
    window: Window
    tolerance_days: int
    csv_in: list[Txn]
    sage_in: list[Txn]
    csv_out: list[Txn]
    sage_out: list[Txn]
    matches: list[Match]
    missing_in_sage: list[Txn]
    extra_in_sage: list[Txn]
    csv_skipped: list[tuple[int, str]] = field(default_factory=list)
    sage_skipped: list[tuple[int, str]] = field(default_factory=list)
    sign_flip_hint: bool = False
    sage_account: str = ""                       # sectioned Sage report: the account compared
    sage_opening: Optional[int] = None           # its Opening Balance, whole file
    sage_closing: Optional[int] = None           # its Closing Balance, whole file
    sage_integrity_ok: Optional[bool] = None     # opening plus every line equals closing

    @property
    def csv_total(self) -> int:
        return sum(t.cents for t in self.csv_in)

    @property
    def sage_total(self) -> int:
        return sum(t.cents for t in self.sage_in)

    @property
    def difference(self) -> int:
        """CSV total minus Sage total for the window."""
        return self.csv_total - self.sage_total

    @property
    def missing_total(self) -> int:
        return sum(t.cents for t in self.missing_in_sage)

    @property
    def extra_total(self) -> int:
        return sum(t.cents for t in self.extra_in_sage)

    @property
    def proof_ok(self) -> bool:
        return self.missing_total - self.extra_total == self.difference

    @property
    def review(self) -> list[Match]:
        return [m for m in self.matches if m.tier in REVIEW_TIERS]

    @property
    def matched_csv_lines(self) -> int:
        return sum(len(m.csv_txns) for m in self.matches)

    @property
    def matched_sage_lines(self) -> int:
        return sum(len(m.sage_txns) for m in self.matches)

    def status_map(self) -> dict[tuple[str, int], str]:
        status: dict[tuple[str, int], str] = {}
        for t in self.csv_out + self.sage_out:
            status[t.key] = STATUS_OUTSIDE
        for m in self.matches:
            for t in m.csv_txns + m.sage_txns:
                status[t.key] = TIER_STATUS[m.tier]
        for t in self.missing_in_sage:
            status[t.key] = STATUS_MISSING
        for t in self.extra_in_sage:
            status[t.key] = STATUS_EXTRA
        return status


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def sage_keys(t: Txn) -> set[str]:
    return {k for k in (normalise(t.description), normalise(t.comment)) if k}


def similarity(csv_txn: Txn, sage_txn: Txn) -> int:
    """Words in common with the better of Description and Comment. Integer, no floats."""
    a = tokens(csv_txn.description)
    return max(len(a & tokens(sage_txn.description)), len(a & tokens(sage_txn.comment)))


def distinctive(text: str) -> set[str]:
    """Alphabetic words of four letters or more that are not stopwords. Letters and
    digits split apart, so 'Plumblink08H35' yields 'plumblink'."""
    return {w for w in re.findall(r"[a-z]+|[0-9]+", (text or "").lower())
            if len(w) >= 4 and w.isalpha() and w not in STOPWORDS}


def evidence(csv_txn: Txn, sage_txn: Txn) -> list[str]:
    """Why a same-date, same-amount pair is more than a coincidence: distinctive words
    the bank text shares with Sage's description or name, or one text found whole
    inside the other. Empty means the pair rests on date and amount alone."""
    shared = distinctive(csv_txn.description) & (distinctive(sage_txn.description)
                                                | distinctive(sage_txn.comment))
    found = sorted(shared)
    c_norm = normalise(csv_txn.description)
    for text in (sage_txn.description, sage_txn.comment):
        s_norm = normalise(text)
        if len(s_norm) < 5 or s_norm in STOPWORDS or s_norm in found or len(c_norm) < 5:
            continue
        if (s_norm in c_norm or c_norm in s_norm) and text.strip() not in found:
            found.append(text.strip())
    return found


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

def auto_window(csv_parsed: Parsed, sage_parsed: Parsed) -> Optional[Window]:
    if not csv_parsed.txns or not sage_parsed.txns:
        return None
    start = max(csv_parsed.first_date, sage_parsed.first_date)
    end = min(csv_parsed.last_date, sage_parsed.last_date)
    if start > end:
        return None
    return Window(start, end)


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def _by_order(txns: list[Txn]) -> list[Txn]:
    return sorted(txns, key=lambda t: (t.date, t.row))


def reconcile(csv_parsed: Parsed, sage_parsed: Parsed,
              window: Optional[Window] = None, tolerance_days: int = 0) -> Result:
    if window is None:
        window = auto_window(csv_parsed, sage_parsed)
    if window is None:
        raise ValueError("The two files share no dates, so there is nothing to compare.")

    csv_in = _by_order([t for t in csv_parsed.txns if window.contains(t.date)])
    csv_out = _by_order([t for t in csv_parsed.txns if not window.contains(t.date)])
    sage_in = _by_order([t for t in sage_parsed.txns if window.contains(t.date)])
    sage_out = _by_order([t for t in sage_parsed.txns if not window.contains(t.date)])

    # Sign check before matching: if flipping the CSV sign would pair more lines
    # than the signs as given, the two files disagree on convention.
    sage_counter = Counter((t.date, t.cents) for t in sage_in)
    direct = sum(1 for t in csv_in if (t.date, t.cents) in sage_counter)
    flipped = sum(1 for t in csv_in if t.cents != 0 and (t.date, -t.cents) in sage_counter)
    sign_flip_hint = flipped > direct

    csv_free: dict[tuple[str, int], Txn] = {t.key: t for t in csv_in}
    sage_free: dict[tuple[str, int], Txn] = {t.key: t for t in sage_in}
    matches: list[Match] = []

    def sage_candidates(when: date, cents: int) -> list[Txn]:
        return [t for t in sage_in if t.key in sage_free and t.date == when and t.cents == cents]

    def claim(tier: str, csv_txns: list[Txn], sage_txns: list[Txn], note: str = "") -> None:
        for t in csv_txns:
            del csv_free[t.key]
        for t in sage_txns:
            del sage_free[t.key]
        matches.append(Match(tier, csv_txns, sage_txns, note))

    # Pass 1: exact.
    for c in csv_in:
        if c.key not in csv_free:
            continue
        want = normalise(c.description)
        for s in sage_candidates(c.date, c.cents):
            if want and want in sage_keys(s):
                claim(TIER_EXACT, [c], [s])
                break

    # Pass 2: same date and amount. Name evidence makes it a "Name" match; none
    # makes it "Amount only", for review. Best evidence, then overlap, then file order.
    for c in csv_in:
        if c.key not in csv_free:
            continue
        cands = sage_candidates(c.date, c.cents)
        if not cands:
            continue
        best = max(cands, key=lambda s: (len(evidence(c, s)), similarity(c, s), -s.row))
        found = evidence(c, best)
        shown = best.comment if best.comment and best.comment != best.description else best.description
        if found:
            claim(TIER_NAME, [c], [best],
                  note=f"Same date and amount, shared: {', '.join(found)}. Sage: {shown}.")
        else:
            claim(TIER_AMOUNT, [c], [best],
                  note=f"Descriptions differ. CSV: {c.description}. Sage: {shown}.")

    # Pass 3a: one CSV line = several Sage lines sharing a reference on that date.
    # Lines with no reference are never grouped: a description is too weak a key
    # and summing unrelated lines is how a recon tool cries wolf.
    for c in csv_in:
        if c.key not in csv_free:
            continue
        groups: dict[str, list[Txn]] = {}
        for s in sage_in:
            if s.key in sage_free and s.date == c.date and s.reference:
                groups.setdefault(s.reference, []).append(s)
        for reference, lines in groups.items():
            if len(lines) >= 2 and sum(t.cents for t in lines) == c.cents:
                claim(TIER_SPLIT, [c], lines,
                      note=f"{len(lines)} Sage lines under reference {reference} add up to the CSV amount.")
                break

    # Pass 3b: one Sage line = several CSV lines with the same description and the
    # same sign on that date (bank fees the bank lists one by one, Sage in one line).
    for s in sage_in:
        if s.key not in sage_free or s.cents == 0:
            continue
        groups2: dict[str, list[Txn]] = {}
        for c in csv_in:
            if c.key in csv_free and c.date == s.date and (c.cents > 0) == (s.cents > 0):
                groups2.setdefault(normalise(c.description), []).append(c)
        for lines in groups2.values():
            if len(lines) >= 2 and sum(t.cents for t in lines) == s.cents:
                claim(TIER_SPLIT, lines, [s],
                      note=f"{len(lines)} CSV lines with the same description add up to the Sage amount.")
                break

    # Pass 4: date differs, only with a tolerance, and only on the same description.
    if tolerance_days > 0:
        for c in csv_in:
            if c.key not in csv_free:
                continue
            want = normalise(c.description)
            if not want:
                continue
            cands = [s for s in sage_in if s.key in sage_free and s.cents == c.cents
                     and s.date != c.date and abs((s.date - c.date).days) <= tolerance_days
                     and want in sage_keys(s)]
            if not cands:
                continue
            best = min(cands, key=lambda s: (abs((s.date - c.date).days), s.row))
            claim(TIER_DATE, [c], [best],
                  note=f"CSV dated {c.date:%d/%m/%Y}, Sage dated {best.date:%d/%m/%Y}.")

    missing = [t for t in csv_in if t.key in csv_free]
    extra = [t for t in sage_in if t.key in sage_free]
    matches.sort(key=lambda m: (m.date, m.csv_txns[0].row))

    return Result(
        window=window, tolerance_days=tolerance_days,
        csv_in=csv_in, sage_in=sage_in, csv_out=csv_out, sage_out=sage_out,
        matches=matches, missing_in_sage=missing, extra_in_sage=extra,
        csv_skipped=list(csv_parsed.skipped), sage_skipped=list(sage_parsed.skipped),
        sign_flip_hint=sign_flip_hint,
        sage_account=sage_parsed.account, sage_opening=sage_parsed.opening,
        sage_closing=sage_parsed.closing, sage_integrity_ok=sage_parsed.integrity_ok,
    )


def rand(cents: Optional[int]) -> str:
    """Integer cents to '1,234.56' without going through a float."""
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{frac:02d}"
