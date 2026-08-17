"""Alias derivation and text normalisation (BUILD.md §4).

Two alias sources feed a supplier's *evidence set* — the tokens we look for inside
a bank line's description:

  * **Derived** (per upload, no storage): the supplier's own payment descriptions.
    Strip the ``NNNNNNNN-NNNN,`` batch prefix, drop generic tokens, keep the rest.
  * **Manual/learned** (Google Sheet ``aliases`` tab): brand mappings derivation
    can't reach, e.g. ``USAVE`` -> Shoprite.

Plus the supplier name itself: its distinctive tokens and its acronym
(``Pick n Pay`` -> ``PNP``, the only evidence that reaches the ``PnP Crp...`` line).

Matching is substring containment in the *squashed* (uppercased, alphanumeric-only,
timestamp-stripped) bank description, because bank text runs words together
(``STEEL AND PIPE08H24 debit card purchase``). BUILD.md §3.4 step 2.
"""

from __future__ import annotations

import re
from typing import Iterable

BATCH_PREFIX = re.compile(r"^\d{8}-\d{4},\s*")
TIMESTAMP = re.compile(r"\d{2}H\d{2}")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_ALNUM_ONLY = re.compile(r"[^A-Z0-9]")

# Generic words carried by payment/bank text that must never become evidence.
STOPWORDS = {
    "PURCHASE", "FROM", "PAYMENT", "PAYMENTS", "PAID", "IB", "TO", "TRANSFER",
    "DEBIT", "CREDIT", "CARD", "FEE", "FEES", "PTY", "LTD", "EDMS", "BPK", "CC",
    "AND", "THE", "OF", "FOR", "IMMEDIATE", "HONOURING", "ACCOUNT", "ACC",
    "REF", "INV", "PAY", "BANK", "CASH", "EFT", "ONLINE", "PURCH", "TFR",
}

MIN_TOKEN_LEN = 3


def squash(text: str) -> str:
    """Uppercase, drop ``HHhMM`` timestamps, strip everything non-alphanumeric.

    This is the *haystack* form for a bank description; evidence tokens are tested
    against it with plain ``in``.
    """
    up = (text or "").upper()
    up = TIMESTAMP.sub("", up)
    return _ALNUM_ONLY.sub("", up)


def tokenize(text: str, *, drop_stopwords: bool = True, min_len: int = MIN_TOKEN_LEN) -> list[str]:
    """Split on non-alphanumerics, uppercase, drop stopwords / pure numbers / short."""
    out: list[str] = []
    seen: set[str] = set()
    cleaned = TIMESTAMP.sub("", (text or "").upper())
    for tok in _NON_ALNUM.split(cleaned):
        if not tok or tok in seen:
            continue
        if tok.isdigit():
            continue
        if len(tok) < min_len:
            continue
        if drop_stopwords and tok in STOPWORDS:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def strip_batch_prefix(description: str) -> str:
    return BATCH_PREFIX.sub("", description or "")


def name_tokens(name: str) -> list[str]:
    return tokenize(name)


def name_acronym(name: str) -> str | None:
    """First letter of each name token (short words kept), if it yields >= 3 chars.

    ``Pick n Pay`` -> ``PNP``. Returned only when >= 2 tokens and length >= 3, so
    it is distinctive enough to gate on alongside an exact amount match.
    """
    parts = [p for p in _NON_ALNUM.split((name or "").upper()) if p]
    if len(parts) < 2:
        return None
    acr = "".join(p[0] for p in parts)
    return acr if len(acr) >= 3 else None


def derive_supplier_aliases(descriptions: Iterable[str]) -> list[str]:
    """Distinctive tokens learned from a supplier's own payment descriptions.

    Only descriptions carrying the batch prefix teach us anything (that prefix is
    the tell that bank statement text follows). Bank ``Supplier Payment`` rows,
    whose description is just the batch ref, teach nothing — hence we key on the
    prefix, not on row type.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for desc in descriptions:
        if not desc or not BATCH_PREFIX.match(desc):
            continue
        for tok in tokenize(strip_batch_prefix(desc)):
            if tok not in seen:
                seen.add(tok)
                tokens.append(tok)
    return tokens


def manual_alias_evidence(pattern: str) -> list[str]:
    """Evidence strings for a manually-seeded alias pattern.

    Adds both the full squashed phrase and its individual tokens, and does NOT
    drop stopwords or length — the human chose this pattern deliberately.
    """
    out: list[str] = []
    seen: set[str] = set()
    squashed = squash(pattern)
    if len(squashed) >= 2:
        out.append(squashed)
        seen.add(squashed)
    for tok in tokenize(pattern, drop_stopwords=False, min_len=2):
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def build_evidence_set(
    supplier_name: str,
    payment_descriptions: Iterable[str],
    manual_patterns: Iterable[str] = (),
) -> list[str]:
    """Full evidence set for a supplier: name tokens + acronym + derived + manual."""
    evidence: list[str] = []
    seen: set[str] = set()

    def add(tok: str) -> None:
        if tok and tok not in seen:
            seen.add(tok)
            evidence.append(tok)

    for t in name_tokens(supplier_name):
        add(t)
    acr = name_acronym(supplier_name)
    if acr:
        add(acr)
    for t in derive_supplier_aliases(payment_descriptions):
        add(t)
    for pat in manual_patterns:
        for t in manual_alias_evidence(pat):
            add(t)
    return evidence


def evidence_hits(evidence: Iterable[str], bank_description: str) -> list[str]:
    """Which evidence tokens appear (as substrings) in the squashed bank text."""
    hay = squash(bank_description)
    return [tok for tok in evidence if tok and tok in hay]
