"""Engine coverage: classification, pairing, typos, combos, opening, cross (§3, §8)."""

from pathlib import Path

from recon.parse import parse_supplier_report, Supplier, SupplierReport, SupplierTxn
from recon.engine import (CAT_GREEN, CAT_INVOICES, CAT_PAYMENTS, LEDGER_GREEN,
                          LEDGER_RED, LEDGER_YELLOW, analyze, analyze_supplier,
                          classify, cross_account_settlements, ledger_rows)

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


def test_cross_account_name_linked_includes_bulk_and_consumes():
    # Agrimark (payments needed) has an unpaid invoice; Elgin Agrimark (bulk,
    # invoices needed) has the matching payment. Shared token AGRIMARK -> name_linked,
    # the bulk side must NOT disqualify it, and both items leave the needed lists.
    agrimark = Supplier("Agrimark", 0, 176815, [_invoice("Agrimark", "SIV1", 176815, 0)])
    elgin_debits = [_payment("Elgin Agrimark", f"P{i}", 800000 + i, 10 + i) for i in range(24)]
    elgin_debits.append(_payment("Elgin Agrimark", "PAY1", 176815, 99))
    elgin = Supplier("Elgin Agrimark", 0, -1_500_000, elgin_debits)

    eng = analyze(SupplierReport(suppliers=[agrimark, elgin]))
    hit = [s for s in eng.settlements if s.amount_cents == 176815]
    assert len(hit) == 1
    s = hit[0]
    assert s.confidence == "name_linked"
    assert s.invoice_supplier == "Agrimark" and s.invoice_ref == "SIV1"
    assert s.payment_supplier == "Elgin Agrimark" and s.payment_ref == "PAY1"
    assert "AGRIMARK" in s.evidence
    # consumed: no longer firing as needed on either side, noted on both, residual updated
    ag, el = eng.get("Agrimark"), eng.get("Elgin Agrimark")
    assert all(t.reference != "SIV1" for t in ag.unmatched_invoices)
    assert all(t.reference != "PAY1" for t in el.unmatched_payments)
    assert any("settled cross-account" in n for n in ag.notes)
    assert any("settled cross-account" in n for n in el.notes)
    assert ag.residual_cents == 0


def test_cross_account_amount_only_surfaced_but_not_consumed():
    blue = Supplier("Blue Traders", 0, 250000, [_invoice("Blue Traders", "SIV2", 250000, 0)])
    red = Supplier("Red Holdings", 0, -250000, [_payment("Red Holdings", "PAY2", 250000, 1)])
    eng = analyze(SupplierReport(suppliers=[blue, red]))
    hit = [s for s in eng.settlements if s.amount_cents == 250000]
    assert len(hit) == 1
    assert hit[0].confidence == "amount_only"
    assert hit[0].evidence == []
    # NOT consumed - amount alone is not confident enough to clear a needed list
    assert any(t.reference == "SIV2" for t in eng.get("Blue Traders").unmatched_invoices)
    assert any(t.reference == "PAY2" for t in eng.get("Red Holdings").unmatched_payments)


def test_cross_account_bulk_without_name_link_is_suppressed():
    green = Supplier("Green Co", 0, 330000, [_invoice("Green Co", "SIV3", 330000, 0)])
    stmt_debits = [_payment("Statement House", f"Q{i}", 700000 + i, 10 + i) for i in range(24)]
    stmt_debits.append(_payment("Statement House", "PAY3", 330000, 99))
    stmt = Supplier("Statement House", 0, -1_500_000, stmt_debits)
    eng = analyze(SupplierReport(suppliers=[green, stmt]))
    assert [s for s in eng.settlements if s.amount_cents == 330000] == []


def test_near_match_tolerance_is_one_rand():
    # 20c keying error (real case: 983.86 vs 983.66) must pair as a typo now,
    # while a diff just over R1.00 must stay unmatched.
    s20 = Supplier("T20", 0, -20, [
        _invoice("T20", "I1", 98366, 0), _payment("T20", "P1", 98386, 1)])
    res = analyze_supplier(s20)
    assert len(res.typos) == 1 and res.typos[0].diff_cents == -20
    assert res.unmatched_invoices == [] and res.unmatched_payments == []

    s101 = Supplier("T101", 0, -101, [
        _invoice("T101", "I1", 98366, 0), _payment("T101", "P1", 98467, 1)])
    res = analyze_supplier(s101)
    assert res.typos == []
    assert len(res.unmatched_invoices) == 1 and len(res.unmatched_payments) == 1


# --- Ledger grading (green <= 10 days, yellow otherwise, red unmatched) ------

def _inv_d(name, ref, cents, date, ri):
    return SupplierTxn(name, date, ref, "Supplier Invoice", "", None, cents, "", ri)


def _pay_d(name, ref, cents, date, ri):
    return SupplierTxn(name, date, ref, "Supplier Payment", "", cents, None, "", ri)


def test_ledger_pair_paid_within_window_after_invoice_is_green():
    s = Supplier("W", 0, 0, [_inv_d("W", "I1", 5000, "01/07/2026", 0),
                             _pay_d("W", "P1", 5000, "08/07/2026", 1)])
    rows = ledger_rows(analyze_supplier(s))
    assert [r.status for r in rows] == [LEDGER_GREEN, LEDGER_GREEN]
    assert "7 days" in rows[0].note


def test_ledger_pair_paid_long_after_invoice_is_yellow():
    s = Supplier("W", 0, 0, [_inv_d("W", "I1", 5000, "01/03/2026", 0),
                             _pay_d("W", "P1", 5000, "20/07/2026", 1)])
    rows = ledger_rows(analyze_supplier(s))
    assert [r.status for r in rows] == [LEDGER_YELLOW, LEDGER_YELLOW]
    assert "days apart" in rows[0].note


def test_ledger_payment_before_invoice_is_never_green():
    # Paid 7 days BEFORE the invoice: same gap that grades green in the valid
    # direction must grade yellow here - you cannot pay an invoice that does
    # not exist yet.
    s = Supplier("W", 0, 0, [_pay_d("W", "P1", 5000, "01/07/2026", 0),
                             _inv_d("W", "I1", 5000, "08/07/2026", 1)])
    rows = ledger_rows(analyze_supplier(s))
    assert [r.status for r in rows] == [LEDGER_YELLOW, LEDGER_YELLOW]
    assert "before the invoice" in rows[0].note


def test_ledger_unmatched_is_red():
    s = Supplier("W", 0, 7000, [_inv_d("W", "I1", 7000, "01/07/2026", 0)])
    rows = ledger_rows(analyze_supplier(s))
    assert rows[0].status == LEDGER_RED
    assert rows[0].note == "no matching payment"


def test_ledger_cross_settled_graded_by_dates():
    agrimark = Supplier("Agrimark", 0, 176815,
                        [_inv_d("Agrimark", "SIV1", 176815, "01/07/2026", 0)])
    elgin = Supplier("Elgin Agrimark", 0, -176815,
                     [_pay_d("Elgin Agrimark", "PAY1", 176815, "03/07/2026", 1)])
    eng = analyze(SupplierReport(suppliers=[agrimark, elgin]))
    ag_rows = ledger_rows(eng.get("Agrimark"))
    assert ag_rows[0].status == LEDGER_GREEN
    assert "cross-account Elgin Agrimark PAY1" in ag_rows[0].note
    # settlement carries both dates for display
    assert eng.settlements[0].invoice_date == "01/07/2026"
    assert eng.settlements[0].payment_date == "03/07/2026"


def test_cross_account_taught_alias_links_unrelated_names():
    # No shared name token, but the user taught supplier "Blue Traders" the alias
    # "Red Holdings" -> the pair is name_linked (and consumed), not amount_only.
    blue = Supplier("Blue Traders", 0, 250000, [_invoice("Blue Traders", "SIV2", 250000, 0)])
    red = Supplier("Red Holdings", 0, -250000, [_payment("Red Holdings", "PAY2", 250000, 1)])
    eng = analyze(SupplierReport(suppliers=[blue, red]),
                  manual_patterns={"Blue Traders": ["Red Holdings"]})
    hit = [s for s in eng.settlements if s.amount_cents == 250000]
    assert len(hit) == 1
    assert hit[0].confidence == "name_linked"
    assert hit[0].evidence  # alias tokens present
    assert eng.get("Blue Traders").unmatched_invoices == []
    assert eng.get("Red Holdings").unmatched_payments == []
