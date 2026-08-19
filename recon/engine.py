"""Recon engine: classification + within-supplier and cross-supplier pairing.

All money is integer cents (BUILD.md §2.2); we never compare floats. Dates are
never a matching signal (§2.2). The engine takes parsed data and returns plain
dataclasses - the Streamlit UI and the Excel export are two renderers over these.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .aliases import derive_supplier_aliases, manual_alias_evidence, name_tokens, squash
from .parse import Supplier, SupplierReport, SupplierTxn

# Bumped on ANY change to result shapes or matching behavior. app.py uses this
# both as the st.cache_data salt AND to detect a stale module surviving a
# Streamlit Cloud redeploy (the process keeps sys.modules across git pushes).
ENGINE_VERSION = 5

# Classification threshold: |closing| < 1 cent is green.
GREEN_EPS = 1
# Near-match (capture typo) tolerance, cents. R1.00: wide enough to catch real
# keying errors like 983.86 vs 983.66 (20c), and safe because a near-match is
# never silently cleared - it always lands on Capture Typos for human review.
TYPO_TOLERANCE = 100
# Combination pass bounds (§3.2): skip when a side exceeds this many items.
COMBO_MAX_ITEMS = 20
COMBO_MAX_SIZE = 4
COMBO_BUDGET = 200_000  # hard cap on combinations evaluated per supplier

CAT_GREEN = "green"
CAT_PAYMENTS = "payments_needed"     # closing credit (>0): invoices exceed payments
CAT_INVOICES = "invoices_needed"     # closing debit  (<0): payments exceed invoices

BULK_NOTE = "bulk/statement account - request supplier statement"
OPENING_NOTE = "opening-balance component - request prior-period ledger"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TypoPair:
    invoice: SupplierTxn   # credit side
    payment: SupplierTxn   # debit side
    diff_cents: int        # signed: invoice.credit - payment.debit


@dataclass
class CombinationMatch:
    target: SupplierTxn
    parts: list[SupplierTxn]
    target_side: str       # 'payment' (debit) or 'invoice' (credit)


@dataclass
class SupplierResult:
    supplier: Supplier
    category: str
    unmatched_invoices: list[SupplierTxn] = field(default_factory=list)  # unmatched credits
    unmatched_payments: list[SupplierTxn] = field(default_factory=list)  # unmatched debits
    matched_pairs: list[tuple[SupplierTxn, SupplierTxn]] = field(default_factory=list)
    typos: list[TypoPair] = field(default_factory=list)
    combinations: list[CombinationMatch] = field(default_factory=list)
    residual_cents: int = 0
    notes: list[str] = field(default_factory=list)
    bulk: bool = False
    opening_component: bool = False
    evidence_tokens: list[str] = field(default_factory=list)   # name + derived aliases
    derived_aliases: list[str] = field(default_factory=list)   # from own payment descriptions
    # items consumed by a name_linked cross-account settlement, with the settlement
    # they belong to - lets the ledger view show and grade them
    settled_items: list[tuple[SupplierTxn, "CrossSettlement"]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.supplier.name

    @property
    def closing(self) -> int:
        return self.supplier.closing


@dataclass
class CrossSupplierFinding:
    supplier_a: str
    supplier_b: str
    amount_cents: Optional[int]
    kind: str               # 'balance_mirror' | 'item_match' | 'name_reference'
    evidence: list[str] = field(default_factory=list)


@dataclass
class CrossSettlement:
    """One needed-invoice of supplier A settled by one needed-payment of supplier B
    (same amount, different accounts) - the Agrimark / Elgin Agrimark case."""
    invoice_supplier: str
    invoice_ref: str
    payment_supplier: str
    payment_ref: str
    amount_cents: int
    confidence: str          # 'name_linked' | 'amount_only'
    evidence: list[str] = field(default_factory=list)
    invoice_date: str = ""
    payment_date: str = ""


@dataclass
class EngineResult:
    suppliers: list[SupplierResult]
    cross: list[CrossSupplierFinding]
    settlements: list[CrossSettlement] = field(default_factory=list)

    def by_category(self, category: str) -> list[SupplierResult]:
        return [r for r in self.suppliers if r.category == category]

    def get(self, name: str) -> Optional[SupplierResult]:
        for r in self.suppliers:
            if r.name == name:
                return r
        return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(supplier: Supplier) -> str:
    if abs(supplier.closing) < GREEN_EPS:
        return CAT_GREEN
    return CAT_PAYMENTS if supplier.closing > 0 else CAT_INVOICES


# ---------------------------------------------------------------------------
# Within-supplier pairing
# ---------------------------------------------------------------------------

def _exact_multiset_pair(
    credits: list[SupplierTxn], debits: list[SupplierTxn]
) -> tuple[list[tuple[SupplierTxn, SupplierTxn]], list[SupplierTxn], list[SupplierTxn]]:
    """Pair equal amounts by count (multisets, not sets - identical amounts recur)."""
    cred_by_amt: dict[int, list[SupplierTxn]] = {}
    deb_by_amt: dict[int, list[SupplierTxn]] = {}
    for t in credits:
        cred_by_amt.setdefault(t.credit, []).append(t)
    for t in debits:
        deb_by_amt.setdefault(t.debit, []).append(t)

    matched: list[tuple[SupplierTxn, SupplierTxn]] = []
    unmatched_c: list[SupplierTxn] = []
    unmatched_d: list[SupplierTxn] = []
    for amt in set(cred_by_amt) | set(deb_by_amt):
        cs = cred_by_amt.get(amt, [])
        ds = deb_by_amt.get(amt, [])
        n = min(len(cs), len(ds))
        matched.extend(zip(cs[:n], ds[:n]))
        unmatched_c.extend(cs[n:])
        unmatched_d.extend(ds[n:])
    unmatched_c.sort(key=lambda t: t.row_index)
    unmatched_d.sort(key=lambda t: t.row_index)
    return matched, unmatched_c, unmatched_d


def _near_match_pass(
    unmatched_c: list[SupplierTxn], unmatched_d: list[SupplierTxn]
) -> tuple[list[TypoPair], list[SupplierTxn], list[SupplierTxn]]:
    """Pair leftovers within TYPO_TOLERANCE cents -> Capture Typos (never silent)."""
    typos: list[TypoPair] = []
    used_d: set[int] = set()
    consumed_c: set[int] = set()
    for ci, c in enumerate(unmatched_c):
        for di, d in enumerate(unmatched_d):
            if di in used_d:
                continue
            diff = c.credit - d.debit
            if 0 < abs(diff) <= TYPO_TOLERANCE:
                typos.append(TypoPair(invoice=c, payment=d, diff_cents=diff))
                used_d.add(di)
                consumed_c.add(ci)
                break
    rem_c = [c for i, c in enumerate(unmatched_c) if i not in consumed_c]
    rem_d = [d for i, d in enumerate(unmatched_d) if i not in used_d]
    return typos, rem_c, rem_d


def _combination_pass(
    unmatched_c: list[SupplierTxn], unmatched_d: list[SupplierTxn]
) -> tuple[list[CombinationMatch], list[SupplierTxn], list[SupplierTxn]]:
    """One item on one side == exact sum of 2..4 items on the other (§3.2).

    Bounded: only when both sides are <= COMBO_MAX_ITEMS, combos <= size 4, and a
    hard evaluation budget. Consumes matched items so nothing is reused.
    """
    if len(unmatched_c) > COMBO_MAX_ITEMS or len(unmatched_d) > COMBO_MAX_ITEMS:
        return [], unmatched_c, unmatched_d

    combos: list[CombinationMatch] = []
    budget = [COMBO_BUDGET]
    c_amt = [t.credit for t in unmatched_c]
    d_amt = [t.debit for t in unmatched_d]
    used_c: set[int] = set()
    used_d: set[int] = set()

    def search(target_amt: int, parts_amt: list[int], used_parts: set[int]) -> Optional[tuple[int, ...]]:
        avail = [i for i in range(len(parts_amt)) if i not in used_parts]
        for size in range(2, COMBO_MAX_SIZE + 1):
            if len(avail) < size or budget[0] <= 0:
                continue
            for combo in itertools.combinations(avail, size):
                budget[0] -= 1
                if budget[0] <= 0:
                    return None
                if sum(parts_amt[i] for i in combo) == target_amt:
                    return combo
        return None

    # one payment (debit) covering N invoices (credits)
    for di, tgt in enumerate(unmatched_d):
        if di in used_d or budget[0] <= 0:
            continue
        hit = search(tgt.debit, c_amt, used_c)
        if hit:
            used_d.add(di)
            used_c.update(hit)
            combos.append(CombinationMatch(target=tgt, parts=[unmatched_c[i] for i in hit], target_side="payment"))
    # one invoice (credit) covered by N payments (debits)
    for ci, tgt in enumerate(unmatched_c):
        if ci in used_c or budget[0] <= 0:
            continue
        hit = search(tgt.credit, d_amt, used_d)
        if hit:
            used_c.add(ci)
            used_d.update(hit)
            combos.append(CombinationMatch(target=tgt, parts=[unmatched_d[i] for i in hit], target_side="invoice"))

    rem_c = [t for i, t in enumerate(unmatched_c) if i not in used_c]
    rem_d = [t for i, t in enumerate(unmatched_d) if i not in used_d]
    return combos, rem_c, rem_d


def analyze_supplier(supplier: Supplier) -> SupplierResult:
    category = classify(supplier)
    credits = [t for t in supplier.txns if t.credit]
    debits = [t for t in supplier.txns if t.debit]

    matched, un_c, un_d = _exact_multiset_pair(credits, debits)
    result = SupplierResult(supplier=supplier, category=category, matched_pairs=matched)

    # Bulk / statement accounts: never brute-force subset-sum (§3.2, Ithuba Fuels).
    if len(un_c) > COMBO_MAX_ITEMS or len(un_d) > COMBO_MAX_ITEMS:
        result.bulk = True
        result.notes.append(BULK_NOTE)
    else:
        typos, un_c, un_d = _near_match_pass(un_c, un_d)
        combos, un_c, un_d = _combination_pass(un_c, un_d)
        result.typos = typos
        result.combinations = combos

    result.unmatched_invoices = sorted(un_c, key=lambda t: t.row_index)
    result.unmatched_payments = sorted(un_d, key=lambda t: t.row_index)
    result.residual_cents = (
        sum(t.credit for t in result.unmatched_invoices)
        - sum(t.debit for t in result.unmatched_payments)
    )

    if supplier.opening != 0:
        result.opening_component = True
        result.notes.append(OPENING_NOTE)

    # Evidence for bank matching (name tokens + derived aliases); manual aliases
    # from the Sheet are merged in by the match layer, which has that context.
    payment_descs = [t.description for t in supplier.txns]
    result.derived_aliases = derive_supplier_aliases(payment_descs)
    result.evidence_tokens = _dedupe(name_tokens(supplier.name) + result.derived_aliases)
    return result


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# Cross-supplier pass (§3.3)
# ---------------------------------------------------------------------------
# The whole risk here is noise: generic location/industry tokens (HERMANUS ×11,
# STEEL ×4, AGRI, MIDAS) and round amounts (500, 1000) create hundreds of bogus
# "duplicate account" pairs that bury the two real signals. We gate on:
#   * distinctiveness - a name token is a cross-reference signal only if it
#     appears in <= 2 supplier names across the whole client (document frequency);
#   * uncommon amounts - an amount is only a mirror/item signal when few suppliers
#     share it, otherwise it needs distinctive name evidence to survive.

MIN_REF_LEN = 4          # tokens shorter than this are never a cross-ref signal
MAX_NAME_DF = 2          # a distinctive token appears in <= this many supplier names


def _name_token_df(results: list[SupplierResult]) -> dict[str, int]:
    df: dict[str, int] = {}
    for r in results:
        for tok in set(name_tokens(r.name)):
            df[tok] = df.get(tok, 0) + 1
    return df


def cross_supplier(results: list[SupplierResult]) -> list[CrossSupplierFinding]:
    findings: list[CrossSupplierFinding] = []
    seen: set[tuple] = set()
    df = _name_token_df(results)

    def distinctive(name: str) -> set[str]:
        return {t for t in name_tokens(name) if len(t) >= MIN_REF_LEN and df.get(t, 0) <= MAX_NAME_DF}

    def name_evidence(a: SupplierResult, b: SupplierResult) -> list[str]:
        """Distinctive tokens shared between A's evidence and B's name (both dirs)."""
        a_ev = set(a.evidence_tokens)
        ev = (a_ev & distinctive(b.name)) | (set(b.evidence_tokens) & distinctive(a.name))
        return sorted(ev)

    reds = [r for r in results if r.category != CAT_GREEN]

    # 1. Whole-balance mirrors: closing A == -closing B. A mirror is only trusted
    #    when the magnitude is distinctive (exactly one +supplier and one -supplier
    #    at that magnitude); coincidental round-number mirrors (three suppliers at
    #    ±500) survive only with distinctive name evidence. Real distinctive hits:
    #    Overstrand/Overberg 6702.21, Hermanus Toyota/Cybed 4341.30.
    pos = [r for r in reds if r.closing > 0]
    neg = [r for r in reds if r.closing < 0]
    mag_pos: dict[int, int] = {}
    mag_neg: dict[int, int] = {}
    for r in pos:
        mag_pos[r.closing] = mag_pos.get(r.closing, 0) + 1
    for r in neg:
        mag_neg[-r.closing] = mag_neg.get(-r.closing, 0) + 1
    for a in pos:
        for b in neg:
            if a.closing != -b.closing:
                continue
            ev = name_evidence(a, b)
            unique_mag = mag_pos.get(a.closing, 0) == 1 and mag_neg.get(a.closing, 0) == 1
            if not unique_mag and not ev:
                continue  # ambiguous round-number mirror with no corroboration
            key = ("balance_mirror", a.name, b.name)
            if key in seen:
                continue
            seen.add(key)
            findings.append(CrossSupplierFinding(
                supplier_a=a.name, supplier_b=b.name,
                amount_cents=a.closing, kind="balance_mirror", evidence=ev,
            ))

    # 2. Item-level exact matches: unmatched invoice of A == unmatched payment of B.
    #    An amount shared by many suppliers (round sums) needs distinctive evidence.
    #    Bulk/statement accounts are excluded - their unmatched lists are hundreds of
    #    items and would collide with everything by chance.
    reds_nb = [r for r in reds if not r.bulk]
    amt_suppliers: dict[int, set[str]] = {}
    for r in reds_nb:
        for t in r.unmatched_invoices:
            amt_suppliers.setdefault(t.credit, set()).add(r.name)
        for t in r.unmatched_payments:
            amt_suppliers.setdefault(t.debit, set()).add(r.name)
    inv_index: dict[int, list[SupplierResult]] = {}
    for r in reds_nb:
        for t in r.unmatched_invoices:
            inv_index.setdefault(t.credit, []).append(r)
    for b in reds_nb:
        for t in b.unmatched_payments:
            for a in inv_index.get(t.debit, []):
                if a.name == b.name:
                    continue
                ev = name_evidence(a, b)
                common = len(amt_suppliers.get(t.debit, set())) > 2
                if common and not ev:
                    continue
                key = ("item_match", a.name, b.name, t.debit)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(CrossSupplierFinding(
                    supplier_a=a.name, supplier_b=b.name,
                    amount_cents=t.debit, kind="item_match", evidence=ev,
                ))

    # 3. Name references: A's *payment descriptions* distinctively name B (possible
    #    duplicate/mis-captured accounts). Keyed on A's derived aliases - what A
    #    actually wrote when paying - not on shared name tokens, so two suppliers
    #    that merely share a town don't pair. Real: Agrimark ↔ Elgin Agrimark / Kaap
    #    Agri Elgin (payments say ELGIN / KAAP).
    for a in results:
        a_derived = set(a.derived_aliases)
        if not a_derived:
            continue
        for b in results:
            if a.name == b.name:
                continue
            overlap = a_derived & distinctive(b.name)
            if not overlap:
                continue
            key = ("name_reference", a.name, b.name)
            if key in seen:
                continue
            seen.add(key)
            findings.append(CrossSupplierFinding(
                supplier_a=a.name, supplier_b=b.name, amount_cents=None,
                kind="name_reference", evidence=sorted(overlap),
            ))
    return findings


# ---------------------------------------------------------------------------
# Cross-account settlement pass: needed-invoice of A <-> needed-payment of B
# ---------------------------------------------------------------------------
# The "second layer": an invoice sitting unpaid on one account may have been
# settled by a payment booked to another account (same real vendor split across
# two accounts, e.g. Agrimark / Elgin Agrimark; or a payment mis-allocated to the
# wrong supplier). Two tiers, both exact-amount:
#   * name_linked - the two names share a distinctive token. Trusted enough to
#     include bulk/statement accounts (whose hundreds of amounts would otherwise
#     collide with everything by chance).
#   * amount_only - exact amount, no shared name. Non-bulk both sides, and the
#     amount must be uncommon (<= MAX_AMOUNT_DF suppliers carry it) or it is
#     suppressed as round-number noise.
# Each invoice and payment is offered at most once; the name_linked pass runs
# first so a strong match is never pre-empted by a coincidental amount-only one.
#
# name_linked settlements CONSUME their items - like the typo and combination
# passes, the matched invoice/payment leaves the unmatched (needed) lists so the
# same item never fires on both Payments Needed and Invoices Needed. Never
# silent: the pair is recorded as a settlement and both suppliers get a note.
# amount_only is surfaced but NOT consumed - not confident enough to clear a
# needed list on its own.

MAX_AMOUNT_DF = 2        # an amount held by more suppliers than this is "common"

CROSS_SETTLED_NOTE = "{n} item(s) settled cross-account with {other} - see Cross-Account"


def cross_account_settlements(
    results: list["SupplierResult"],
    manual_patterns: Optional[dict[str, list[str]]] = None,
) -> list[CrossSettlement]:
    reds = [r for r in results if r.category != CAT_GREEN]
    df = _name_token_df(results)
    manual_patterns = manual_patterns or {}

    def distinctive(name: str) -> set[str]:
        return {t for t in name_tokens(name) if len(t) >= MIN_REF_LEN and df.get(t, 0) <= MAX_NAME_DF}

    def alias_link(a: SupplierResult, b: SupplierResult) -> set[str]:
        """Taught aliases as same-vendor evidence: a pattern saved for supplier X
        that reaches Y's *name* links the two accounts (e.g. teach Agrimark the
        alias "Elgin Agrimark"). Human-chosen, so no document-frequency gate -
        just a length floor so a tiny pattern can't link everything."""
        hits: set[str] = set()
        for x, y in ((a, b), (b, a)):
            hay = squash(y.name)
            for pat in manual_patterns.get(x.name, []):
                hits.update(t for t in manual_alias_evidence(pat)
                            if len(t) >= MIN_REF_LEN and t in hay)
        return hits

    def shared(a: SupplierResult, b: SupplierResult) -> list[str]:
        ev = (set(a.evidence_tokens) & distinctive(b.name)) | (set(b.evidence_tokens) & distinctive(a.name))
        return sorted(ev | alias_link(a, b))

    inv_by_amt: dict[int, list[tuple[SupplierResult, SupplierTxn]]] = {}
    amt_suppliers: dict[int, set[str]] = {}
    for r in reds:
        for t in r.unmatched_invoices:
            inv_by_amt.setdefault(t.credit, []).append((r, t))
            amt_suppliers.setdefault(t.credit, set()).add(r.name)
        for t in r.unmatched_payments:
            amt_suppliers.setdefault(t.debit, set()).add(r.name)

    used_inv: set[int] = set()
    used_pay: set[int] = set()
    out: list[CrossSettlement] = []
    # (invoice-side result, invoice, payment-side result, payment) - name_linked only
    consumed: list[tuple[SupplierResult, SupplierTxn, SupplierResult, SupplierTxn]] = []

    def run(name_linked_only: bool) -> None:
        for b in reds:
            for pay in b.unmatched_payments:
                if id(pay) in used_pay:
                    continue
                for a, inv in inv_by_amt.get(pay.debit, []):
                    if a.name == b.name or id(inv) in used_inv:
                        continue
                    ev = shared(a, b)
                    if name_linked_only:
                        if not ev:
                            continue
                    else:
                        if ev or a.bulk or b.bulk:
                            continue
                        if len(amt_suppliers.get(pay.debit, set())) > MAX_AMOUNT_DF:
                            continue
                    used_inv.add(id(inv))
                    used_pay.add(id(pay))
                    settlement = CrossSettlement(
                        invoice_supplier=a.name, invoice_ref=inv.reference,
                        payment_supplier=b.name, payment_ref=pay.reference,
                        amount_cents=pay.debit,
                        confidence="name_linked" if ev else "amount_only",
                        evidence=ev,
                        invoice_date=inv.date, payment_date=pay.date,
                    )
                    if name_linked_only:
                        consumed.append((a, inv, b, pay))
                        a.settled_items.append((inv, settlement))
                        b.settled_items.append((pay, settlement))
                    out.append(settlement)
                    break

    run(name_linked_only=True)
    run(name_linked_only=False)

    # Consume name_linked items out of the needed lists (never silent: recorded
    # above, and noted on both suppliers below). Residuals are recomputed so the
    # remaining imbalance reflects only genuinely unexplained items.
    if consumed:
        gone = {id(inv) for _, inv, _, _ in consumed} | {id(pay) for _, _, _, pay in consumed}
        counterparts: dict[int, tuple[SupplierResult, dict[str, int]]] = {}
        for a, _inv, b, _pay in consumed:
            for r, other in ((a, b.name), (b, a.name)):
                entry = counterparts.setdefault(id(r), (r, {}))
                entry[1][other] = entry[1].get(other, 0) + 1
        for r, per_other in counterparts.values():
            r.unmatched_invoices = [t for t in r.unmatched_invoices if id(t) not in gone]
            r.unmatched_payments = [t for t in r.unmatched_payments if id(t) not in gone]
            r.residual_cents = (sum(t.credit for t in r.unmatched_invoices)
                                - sum(t.debit for t in r.unmatched_payments))
            for other, n in sorted(per_other.items()):
                r.notes.append(CROSS_SETTLED_NOTE.format(n=n, other=other))
    return out


# ---------------------------------------------------------------------------
# Ledger view: every transaction graded green / yellow / red
# ---------------------------------------------------------------------------
# Mirrors the source report (all txns, all dates, in ledger order) with a status
# per row. Dates are still never a MATCHING signal (§2.2) - they grade the
# CONFIDENCE of matches already made on amount:
#   green  - matched, and the two dates are within MATCH_WINDOW_DAYS of each other
#   yellow - same amount matched but far apart in time (possibly a different
#            payment), a near-match typo, or an out-of-window combination
#   red    - no matching counterpart at all

MATCH_WINDOW_DAYS = 10

LEDGER_GREEN = "green"
LEDGER_YELLOW = "yellow"
LEDGER_RED = "red"


@dataclass
class LedgerRow:
    txn: SupplierTxn
    status: str            # green | yellow | red | "" (informational row)
    note: str              # counterpart + day gap, or the reason it is red


def _parse_dmy(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _days_apart(d1: str, d2: str) -> Optional[int]:
    a, b = _parse_dmy(d1), _parse_dmy(d2)
    if a is None or b is None:
        return None
    return abs((a - b).days)


def ledger_rows(result: SupplierResult) -> list[LedgerRow]:
    status: dict[int, tuple[str, str]] = {}

    def mark(t: SupplierTxn, st: str, note: str) -> None:
        status[id(t)] = (st, note)

    for inv, pay in result.matched_pairs:
        days = _days_apart(inv.date, pay.date)
        if days is not None and days <= MATCH_WINDOW_DAYS:
            mark(inv, LEDGER_GREEN, f"paired with {pay.reference or 'payment'} ({days} days)")
            mark(pay, LEDGER_GREEN, f"paired with {inv.reference or 'invoice'} ({days} days)")
        else:
            gap = "dates unreadable" if days is None else f"{days} days apart"
            mark(inv, LEDGER_YELLOW, f"same amount as {pay.reference or 'payment'} ({gap})")
            mark(pay, LEDGER_YELLOW, f"same amount as {inv.reference or 'invoice'} ({gap})")

    for tp in result.typos:
        d = f"diff R{abs(tp.diff_cents) / 100:.2f}"
        mark(tp.invoice, LEDGER_YELLOW, f"near amount {tp.payment.reference or '-'} ({d})")
        mark(tp.payment, LEDGER_YELLOW, f"near amount {tp.invoice.reference or '-'} ({d})")

    for cm in result.combinations:
        spans = [_days_apart(cm.target.date, p.date) for p in cm.parts]
        within = all(s is not None and s <= MATCH_WINDOW_DAYS for s in spans)
        st = LEDGER_GREEN if within else LEDGER_YELLOW
        refs = " + ".join(p.reference or "-" for p in cm.parts)
        mark(cm.target, st, f"combination of {refs}")
        for p in cm.parts:
            mark(p, st, f"part of combination for {cm.target.reference or '-'}")

    for t, s in result.settled_items:
        if t.credit:
            other_sup, other_ref, other_date = s.payment_supplier, s.payment_ref, s.payment_date
        else:
            other_sup, other_ref, other_date = s.invoice_supplier, s.invoice_ref, s.invoice_date
        days = _days_apart(t.date, other_date)
        st = LEDGER_GREEN if days is not None and days <= MATCH_WINDOW_DAYS else LEDGER_YELLOW
        gap = "dates unreadable" if days is None else f"{days} days"
        mark(t, st, f"cross-account {other_sup} {other_ref or '-'} ({gap})")

    for t in result.unmatched_invoices:
        mark(t, LEDGER_RED, "no matching payment")
    for t in result.unmatched_payments:
        mark(t, LEDGER_RED, "no matching invoice")

    out: list[LedgerRow] = []
    for t in sorted(result.supplier.txns, key=lambda x: x.row_index):
        st, note = status.get(id(t), ("", ""))
        out.append(LedgerRow(txn=t, status=st, note=note))
    return out


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def analyze(report: SupplierReport,
            manual_patterns: Optional[dict[str, list[str]]] = None) -> EngineResult:
    results = [analyze_supplier(s) for s in report.suppliers]
    cross = cross_supplier(results)
    settlements = cross_account_settlements(results, manual_patterns)
    return EngineResult(suppliers=results, cross=cross, settlements=settlements)
