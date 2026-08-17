"""Bank-candidate search for Payments Needed suppliers (BUILD.md §3.4).

For each unmatched invoice we look for the bank payment that settled it but was
booked to a GL account instead of the supplier. The whole credibility of the tool
lives in the verdict tiers: we would rather say "ambiguous" than name a wrong pick.

Search space is bank ``Account Payment`` rows ONLY — a ``Supplier Payment`` row is
already in some supplier's ledger, so offering it would re-allocate someone else's
match. Amounts come from the Credit column (money out, inverted semantics §2.4).
Dates are never used (§2.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .aliases import evidence_hits, manual_alias_evidence, name_acronym
from .engine import CAT_PAYMENTS, SupplierResult
from .parse import BankReport, BankTxn, SupplierTxn

# An amount matching more than this many bank lines is "common" (R155 honouring
# fee, R500 round sums) — needs description evidence or it is ambiguous.
COMMON_AMOUNT_THRESHOLD = 5
# Never dump 40 candidate lines at the accountant; cap and say how many were cut.
CANDIDATE_CAP = 8

VERDICT_CONFIDENT = "confident"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_NONE = "none"

NONE_NOTE = "invoice needs a payment captured; not found in this bank file"


@dataclass
class Candidate:
    txn: BankTxn
    matched_tokens: list[str]

    @property
    def score(self) -> int:
        return len(self.matched_tokens)

    @property
    def has_evidence(self) -> bool:
        return bool(self.matched_tokens)


@dataclass
class MatchResult:
    supplier: str
    invoice: SupplierTxn
    amount_cents: int
    verdict: str
    candidates: list[Candidate] = field(default_factory=list)
    total_hits: int = 0            # exact-amount hits before capping
    capped: bool = False
    note: str = ""

    @property
    def top(self) -> Optional[Candidate]:
        """The single pick — only ever set for a confident verdict (§3.4)."""
        return self.candidates[0] if self.verdict == VERDICT_CONFIDENT and self.candidates else None


def account_payment_index(bank: BankReport) -> dict[int, list[BankTxn]]:
    """Map exact amount-out (cents) -> Account Payment rows. Built once per upload."""
    index: dict[int, list[BankTxn]] = {}
    for t in bank.all_txns:
        if t.txn_type == "Account Payment" and t.amount_out is not None:
            index.setdefault(t.amount_out, []).append(t)
    return index


def _rank(candidates: list[Candidate]) -> list[Candidate]:
    # Evidence first, then by score, then stable by row for reproducibility.
    return sorted(candidates, key=lambda c: (-c.score, c.txn.row_index))


def search_invoice(
    invoice: SupplierTxn,
    amount_cents: int,
    evidence: Iterable[str],
    index: dict[int, list[BankTxn]],
    supplier_name: str,
) -> MatchResult:
    hits = index.get(amount_cents, [])
    result = MatchResult(supplier=supplier_name, invoice=invoice,
                         amount_cents=amount_cents, verdict=VERDICT_NONE,
                         total_hits=len(hits))
    if not hits:
        result.note = NONE_NOTE
        return result

    evidence = list(evidence)
    scored = [Candidate(t, evidence_hits(evidence, t.description)) for t in hits]
    with_ev = [c for c in scored if c.has_evidence]
    common = len(hits) > COMMON_AMOUNT_THRESHOLD

    # Unique top by evidence score decides "confident".
    winners: list[Candidate] = []
    if with_ev:
        top_score = max(c.score for c in with_ev)
        winners = [c for c in with_ev if c.score == top_score]

    if len(winners) == 1:
        result.verdict = VERDICT_CONFIDENT
        result.candidates = winners
        return result

    # Otherwise ambiguous — list candidates, never mark a top pick.
    result.verdict = VERDICT_AMBIGUOUS
    ranked = _rank(scored)
    if common:
        shown = _rank(with_ev)[:CANDIDATE_CAP] if with_ev else ranked[:CANDIDATE_CAP]
        result.candidates = shown
        result.capped = len(shown) < len(hits)
        if with_ev:
            result.note = (f"{len(hits)} bank lines match this amount; "
                           f"{len(with_ev)} carry name evidence but none is unique.")
        else:
            result.note = (f"{len(hits)} bank lines match on amount alone "
                           f"(common amount); showing {len(shown)}.")
    else:
        result.candidates = ranked
        if not with_ev and len(hits) == 1:
            result.note = "single candidate on amount only — no description evidence."
        elif not with_ev:
            result.note = f"{len(hits)} candidates, all on amount only — no description evidence."
        else:
            result.note = f"{len(with_ev)} candidates carry name evidence but tie — no single pick."
    return result


def supplier_evidence(result: SupplierResult, manual_patterns: Iterable[str] = ()) -> list[str]:
    """Full evidence set: name tokens + acronym + derived aliases + manual aliases.

    The acronym (``Pick n Pay`` -> ``PNP``) is the only evidence that reaches the
    ``PnP Crp...`` bank line, so it is scoped to bank matching here rather than the
    engine's cross-supplier evidence, where 3-letter acronyms would add noise.
    """
    evidence = list(result.evidence_tokens)
    seen = set(evidence)
    acr = name_acronym(result.name)
    if acr and acr not in seen:
        seen.add(acr)
        evidence.append(acr)
    for pat in manual_patterns:
        for tok in manual_alias_evidence(pat):
            if tok not in seen:
                seen.add(tok)
                evidence.append(tok)
    return evidence


def match_supplier(
    result: SupplierResult,
    index: dict[int, list[BankTxn]],
    manual_patterns: Iterable[str] = (),
) -> list[MatchResult]:
    """Search bank candidates for each of a Payments Needed supplier's unmatched
    invoices. Bulk/statement accounts are skipped (§3.2) — the label stands in."""
    if result.category != CAT_PAYMENTS or result.bulk:
        return []
    evidence = supplier_evidence(result, manual_patterns)
    out: list[MatchResult] = []
    for inv in result.unmatched_invoices:
        out.append(search_invoice(inv, inv.credit, evidence, index, result.name))
    return out
