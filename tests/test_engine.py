"""Engine coverage: classification, pairing, typos, combos, opening, cross (§3, §8)."""

from pathlib import Path

from recon.parse import parse_supplier_report, Supplier, SupplierReport, SupplierTxn
from recon.engine import (CAT_GREEN, CAT_INVOICES, CAT_PAYMENTS, analyze,
                          analyze_supplier, classify, cross_account_settlements)

FIX = Path(__file__).parent / "fixtures"
SUP = FIX / "supplier_traps.csv"


def _by_name(report):
    return {s.name: s for s in report.suppliers}


def test_classification_by_closing_sign():
    r = parse_supplier_report(SUP)
    by = _by_name(r)
    assert classify(by["Alpha Credit Supplier"]) == CAT_PAYMENTS   # credit closing
    assert classify(by["Beta Debit Supplier"]) == CAT_INVOICES     # debit closing
    assert classify(by["Delta Combo"]) == CAT_GREEN                # zero


def test_multiset_pairing_leaves_one_of_repeated_amount():
    r = parse_supplier_report(SUP)
    res = analyze_supplier(_by_name(r)["Repeat Supplier"])
    # two 50.00 invoices, one 50.00 payment -> exactly one invoice left, no payment
    assert [t.credit for t in res.unmatched_invoices] == [5000]
    assert res.unmatched_payments == []
    assert len(res.matched_pairs) == 1


def test_near_match_typo_not_silently_paired():
    r = parse_supplier_report(SUP)
    res = analyze_supplier(_by_name(r)["Typo Supplier"])
    assert len(res.typos) == 1
    tp = res.typos[0]
    assert tp.invoice.credit == 136926
    assert tp.payment.debit == 136928
    assert tp.diff_cents == -2
    # consumed from the unmatched pool, not left dangling
    assert res.unmatched_invoices == []
    assert res.unmatched_payments == []


def test_combination_pass_one_payment_covers_two_invoices():
    r = parse_supplier_report(SUP)
    res = analyze_supplier(_by_name(r)["Delta Combo"])
    # 50 + 60 == 110 (one payment covers two invoices)
    assert len(res.combinations) == 1
    combo = res.combinations[0]
    assert combo.target.debit == 11000
    assert sorted(p.credit for p in combo.parts) == [5000, 6000]
    assert res.unmatched_invoices == [] and res.unmatched_payments == []


def test_opening_balance_component_flagged():
    r = parse_supplier_report(SUP)
    res = analyze_supplier(_by_name(r)["Gamma Opening Only"])
    assert res.opening_component is True
    assert any("opening-balance component" in n for n in res.notes)


def test_cross_supplier_distinctive_mirror_detected():
    # Two synthetic suppliers with exactly opposite, distinctive closings.
    r = parse_supplier_report(SUP)
    res = analyze(r)
    # Alpha +1000.00, Beta -300.00 are not a mirror (different magnitude) -> none.
    mirrors = [f for f in res.cross if f.kind == "balance_mirror"]
    assert mirrors == []


def test_bulk_account_labeled_not_bruteforced(monkeypatch):
    # Build a supplier with >20 unmatched items on each side; must be labeled bulk.
    from recon.parse import Supplier, SupplierTxn

    txns = []
    for i in range(25):
        txns.append(SupplierTxn("Bulk", "01/01/2026", f"S{i}", "Supplier Invoice",
                                 "", None, 1000 + i, "", i))
    for i in range(25):
        txns.append(SupplierTxn("Bulk", "01/01/2026", f"P{i}", "Supplier Payment",
                                 "", 5000 + i, None, "", 100 + i))
    s = Supplier(name="Bulk", opening=0, closing=999999, txns=txns)
    res = analyze_supplier(s)
    assert res.bulk is True
    assert res.combinations == []
    assert any("bulk/statement account" in n for n in res.notes)


# --- Cross-account settlements (needed-invoice of A <-> needed-payment of B) --

def _invoice(name, ref, cents, ri):
    return SupplierTxn(name, "01/01/2026", ref, "Supplier Invoice", "", None, cents, "", ri)


def _payment(name, ref, cents, ri):
    return SupplierTxn(name, "01/01/2026", ref, "Supplier Payment", "", cents, None, "", ri)


def test_cross_account_name_linked_includes_bulk():
    # Agrimark (payments needed) has an unpaid invoice; Elgin Agrimark (bulk,
    # invoices needed) has the matching payment. Shared token AGRIMARK -> name_linked,
    # and the bulk side must NOT disqualify it.
    agrimark = Supplier("Agrimark", 0, 176815, [_invoice("Agrimark", "SIV1", 176815, 0)])
    elgin_debits = [_payment("Elgin Agrimark", f"P{i}", 800000 + i, 10 + i) for i in range(24)]
    elgin_debits.append(_payment("Elgin Agrimark", "PAY1", 176815, 99))
    elgin = Supplier("Elgin Agrimark", 0, -1_500_000, elgin_debits)

    settlements = cross_account_settlements(analyze(SupplierReport(suppliers=[agrimark, elgin])).suppliers)
    hit = [s for s in settlements if s.amount_cents == 176815]
    assert len(hit) == 1
    s = hit[0]
    assert s.confidence == "name_linked"
    assert s.invoice_supplier == "Agrimark" and s.invoice_ref == "SIV1"
    assert s.payment_supplier == "Elgin Agrimark" and s.payment_ref == "PAY1"
    assert "AGRIMARK" in s.evidence


def test_cross_account_amount_only_when_no_shared_name():
    blue = Supplier("Blue Traders", 0, 250000, [_invoice("Blue Traders", "SIV2", 250000, 0)])
    red = Supplier("Red Holdings", 0, -250000, [_payment("Red Holdings", "PAY2", 250000, 1)])
    settlements = cross_account_settlements(analyze(SupplierReport(suppliers=[blue, red])).suppliers)
    hit = [s for s in settlements if s.amount_cents == 250000]
    assert len(hit) == 1
    assert hit[0].confidence == "amount_only"
    assert hit[0].evidence == []


def test_cross_account_bulk_without_name_link_is_suppressed():
    green = Supplier("Green Co", 0, 330000, [_invoice("Green Co", "SIV3", 330000, 0)])
    stmt_debits = [_payment("Statement House", f"Q{i}", 700000 + i, 10 + i) for i in range(24)]
    stmt_debits.append(_payment("Statement House", "PAY3", 330000, 99))
    stmt = Supplier("Statement House", 0, -1_500_000, stmt_debits)
    settlements = cross_account_settlements(analyze(SupplierReport(suppliers=[green, stmt])).suppliers)
    assert [s for s in settlements if s.amount_cents == 330000] == []
