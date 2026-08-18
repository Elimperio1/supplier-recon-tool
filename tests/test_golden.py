"""Golden numbers (BUILD.md §9) - the build MUST reproduce these on the two real
Sage exports. Skips automatically when the client CSVs are absent (fresh clone /
CI), since they are gitignored client data.

A disagreement here means a real regression: fix the build, not the numbers
(unless the CSVs themselves changed - then re-measure and update §9 and this file).
"""

from pathlib import Path

import pytest

from recon.parse import parse_supplier_report, parse_bank_report
from recon.engine import analyze, CAT_GREEN, CAT_PAYMENTS, CAT_INVOICES
from recon.match import account_payment_index, match_supplier, VERDICT_CONFIDENT, VERDICT_AMBIGUOUS

ROOT = Path(__file__).resolve().parents[1]
SUP = ROOT / "SupplierTransactionsReport (5).csv"
BANK = ROOT / "BanksAndCreditCardsTransactionsReport (2).csv"

pytestmark = pytest.mark.skipif(
    not (SUP.exists() and BANK.exists()),
    reason="real client CSVs not present (gitignored)",
)


@pytest.fixture(scope="module")
def built():
    supplier_report = parse_supplier_report(SUP)
    bank_report = parse_bank_report(BANK)
    engine = analyze(supplier_report)
    idx = account_payment_index(bank_report)
    manual = {"Shoprite": ["USAVE"]}
    matches = {r.name: match_supplier(r, idx, manual.get(r.name, []))
               for r in engine.by_category(CAT_PAYMENTS)}
    return supplier_report, bank_report, engine, matches


def _find(engine, needle):
    return next(r for r in engine.suppliers if needle.lower() in r.name.lower())


# -- headline counts --------------------------------------------------------

def test_supplier_counts(built):
    sr, br, engine, _ = built
    assert len(engine.suppliers) == 105
    assert len(engine.by_category(CAT_GREEN)) == 59
    assert len(engine.by_category(CAT_PAYMENTS)) == 15
    assert len(engine.by_category(CAT_INVOICES)) == 31
    assert len(sr.integrity_failures) == 0


def test_bank_counts(built):
    _, br, _, _ = built
    assert len(br.accounts) == 3
    assert len(br.all_txns) == 1849
    from collections import Counter
    types = Counter(t.txn_type for t in br.all_txns)
    assert types["Account Payment"] == 1148
    assert types["Supplier Payment"] == 523
    assert types["Account Receipt"] == 96
    assert types["Customer Receipt"] == 80
    assert types["VAT Payment"] == 2
    assert len(br.integrity_failures) == 0


# -- named findings ---------------------------------------------------------

def _match_for(engine, matches, needle, amount_cents):
    r = _find(engine, needle)
    return next(m for m in matches[r.name] if m.amount_cents == amount_cents)


def test_pick_n_pay_confident(built):
    _, _, engine, matches = built
    m = _match_for(engine, matches, "Pick n Pay", 15798)
    assert m.verdict == VERDICT_CONFIDENT
    assert "PnP" in m.top.txn.description
    assert m.top.txn.allocation == "Staff Welfare"


def test_lmrc_confident(built):
    _, _, engine, matches = built
    m = _match_for(engine, matches, "LMRC STIHL", 10355)
    assert m.verdict == VERDICT_CONFIDENT
    assert m.top.txn.allocation == "Repair And Maintenance"


def test_steel_and_pipes_two_confident(built):
    _, _, engine, matches = built
    for cents in (21075, 28394):
        m = _match_for(engine, matches, "Steel & Pipes", cents)
        assert m.verdict == VERDICT_CONFIDENT
        assert "STEEL AND PIPE" in m.top.txn.description


def test_shoprite_only_via_usave_alias(built):
    _, br, engine, matches = built
    idx = account_payment_index(br)
    r = _find(engine, "Shoprite")
    # With the USAVE alias -> confident.
    with_alias = match_supplier(r, idx, ["USAVE"])
    m = next(x for x in with_alias if x.amount_cents == 49833)
    assert m.verdict == VERDICT_CONFIDENT
    assert "USave" in m.top.txn.description
    # Without it -> not confident (only amount evidence).
    without = next(x for x in match_supplier(r, idx, []) if x.amount_cents == 49833)
    assert without.verdict != VERDICT_CONFIDENT


def test_cape_agricultural_confident_yoco(built):
    _, _, engine, matches = built
    m = _match_for(engine, matches, "Cape Agricultural", 126500)
    assert m.verdict == VERDICT_CONFIDENT
    assert "Yoco" in m.top.txn.description


def test_robertson_shell_ambiguous(built):
    _, _, engine, matches = built
    m = _match_for(engine, matches, "Robertson Shell", 50000)
    assert m.verdict == VERDICT_AMBIGUOUS
    assert m.total_hits >= 3
    assert m.top is None                # never a top pick


def test_cross_supplier_mirrors(built):
    _, _, engine, _ = built
    mirrors = {(f.supplier_a, f.supplier_b): f for f in engine.cross
               if f.kind == "balance_mirror"}
    pairs = {frozenset(k) for k in mirrors}
    assert frozenset({"Overstrand Munisipality", "Overberg Steel & Irrigation"}) in pairs
    assert frozenset({"Hermanus Toyota", "Cybed Trading"}) in pairs
    for f in mirrors.values():
        if "Overstrand" in f.supplier_a or "Overstrand" in f.supplier_b:
            assert abs(f.amount_cents) == 670221
        if "Toyota" in f.supplier_a or "Toyota" in f.supplier_b:
            assert abs(f.amount_cents) == 434130


def test_ithuba_bulk_labeled(built):
    _, _, engine, _ = built
    r = _find(engine, "Ithuba")
    assert r.bulk is True
    assert any("bulk/statement account" in n for n in r.notes)


def test_eli001_opening_component(built):
    _, _, engine, _ = built
    r = _find(engine, "El Imperio")
    assert r.opening_component is True
    assert r.supplier.opening == 837488
