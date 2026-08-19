"""Bank-candidate search: verdict tiers, search space, common-amount guard (§3.4)."""

from pathlib import Path

from recon.parse import (BankReport, BankAccount, BankTxn, Supplier, SupplierTxn,
                         parse_bank_report)
from recon.engine import analyze_supplier
from recon.match import (VERDICT_AMBIGUOUS, VERDICT_CONFIDENT, VERDICT_NONE,
                         account_payment_index, match_supplier, search_invoice)

FIX = Path(__file__).parent / "fixtures"
BANK = FIX / "bank_basic.csv"


def _bank_txn(amount_out, desc, rowidx, ttype="Account Payment", alloc="GL", ref="R",
              date="01/01/2026"):
    return BankTxn(account="8400", row_index=rowidx, date=date, payee="",
                   description=desc, reference=ref, txn_type=ttype, allocation=alloc,
                   debit=None, credit=amount_out, balance_raw="")


def _inv(amount, ref="SIV", date="01/01/2026"):
    return SupplierTxn("S", date, ref, "Supplier Invoice", "inv", None, amount, "", 1)


# -- search space -----------------------------------------------------------

def test_index_only_account_payments():
    b = parse_bank_report(BANK)
    idx = account_payment_index(b)
    assert 15798 in idx                 # Account Payment (PnP)
    assert 50000 not in idx             # Supplier Payment excluded
    assert 10000 in idx and 20000 in idx and 5000 in idx


# -- verdict tiers ----------------------------------------------------------

def test_confident_single_candidate_with_evidence():
    idx = {15798: [_bank_txn(15798, "PnP Crp Grabou08H52 debit card purchase", 4,
                             alloc="Staff Welfare")]}
    res = search_invoice(_inv(15798), 15798, ["PNP"], idx, "Pick n Pay")
    assert res.verdict == VERDICT_CONFIDENT
    assert res.top is not None
    assert res.top.matched_tokens == ["PNP"]


def test_none_when_no_amount_hit():
    res = search_invoice(_inv(99999), 99999, ["ANY"], {}, "X")
    assert res.verdict == VERDICT_NONE
    assert "not found" in res.note


def test_ambiguous_single_candidate_amount_only():
    idx = {10000: [_bank_txn(10000, "split a", 6, alloc="GL One")]}
    res = search_invoice(_inv(10000), 10000, ["ZZZ"], idx, "X")
    assert res.verdict == VERDICT_AMBIGUOUS
    assert res.top is None               # never a top pick on amount alone
    assert "amount only" in res.note


def test_ambiguous_tie_lists_all_no_top():
    idx = {5000: [_bank_txn(5000, "STEEL one", 1), _bank_txn(5000, "STEEL two", 2)]}
    res = search_invoice(_inv(5000), 5000, ["STEEL"], idx, "X")
    assert res.verdict == VERDICT_AMBIGUOUS
    assert res.top is None
    assert len(res.candidates) == 2


def test_common_amount_guard_caps_and_flags():
    hits = [_bank_txn(50000, f"immediate payment {i}", i) for i in range(8)]
    res = search_invoice(_inv(50000), 50000, ["NOPE"], {50000: hits}, "Robertson Shell")
    assert res.verdict == VERDICT_AMBIGUOUS
    assert res.total_hits == 8
    assert len(res.candidates) <= 8
    assert "amount alone" in res.note


def test_common_amount_unique_evidence_still_confident():
    hits = [_bank_txn(50000, f"immediate payment {i}", i) for i in range(6)]
    hits.append(_bank_txn(50000, "ROBERTSON SHELL fuel", 99))
    res = search_invoice(_inv(50000), 50000, ["ROBERTSON"], {50000: hits}, "Robertson Shell")
    assert res.verdict == VERDICT_CONFIDENT
    assert res.top.matched_tokens == ["ROBERTSON"]


# -- feasibility: a payment cannot predate its invoice -----------------------

def test_candidates_predating_invoice_excluded():
    # Two amount hits, one before the invoice date and one after with evidence;
    # only the later one is a candidate, so it wins confident.
    idx = {15798: [
        _bank_txn(15798, "PNP earlier purchase", 1, date="01/06/2026"),
        _bank_txn(15798, "PNP later purchase", 2, date="20/06/2026"),
    ]}
    res = search_invoice(_inv(15798, date="15/06/2026"), 15798, ["PNP"], idx, "Pick n Pay")
    assert res.verdict == VERDICT_CONFIDENT
    assert res.total_hits == 1
    assert res.top.txn.date == "20/06/2026"


def test_all_candidates_predate_invoice_is_none_with_reason():
    idx = {15798: [_bank_txn(15798, "PNP purchase", 1, date="01/06/2026")]}
    res = search_invoice(_inv(15798, date="15/06/2026"), 15798, ["PNP"], idx, "Pick n Pay")
    assert res.verdict == VERDICT_NONE
    assert "predate the invoice" in res.note


def test_same_day_candidate_is_feasible():
    idx = {15798: [_bank_txn(15798, "PNP purchase", 1, date="15/06/2026")]}
    res = search_invoice(_inv(15798, date="15/06/2026"), 15798, ["PNP"], idx, "Pick n Pay")
    assert res.verdict == VERDICT_CONFIDENT


# -- integration ------------------------------------------------------------

def test_match_supplier_end_to_end_confident_via_acronym():
    b = parse_bank_report(BANK)
    idx = account_payment_index(b)
    supplier = Supplier(name="Pick n Pay", opening=0, closing=15798, txns=[
        SupplierTxn("Pick n Pay", "01/01/2026", "SIV1", "Supplier Invoice", "303128",
                    None, 15798, "", 1)])
    res = analyze_supplier(supplier)
    mrs = match_supplier(res, idx)
    assert len(mrs) == 1
    assert mrs[0].verdict == VERDICT_CONFIDENT
    assert "PNP" in mrs[0].top.matched_tokens


def test_bulk_supplier_not_searched():
    b = parse_bank_report(BANK)
    idx = account_payment_index(b)
    txns = [SupplierTxn("B", "01/01/2026", f"S{i}", "Supplier Invoice", "", None,
                        1000 + i, "", i) for i in range(25)]
    txns += [SupplierTxn("B", "01/01/2026", f"P{i}", "Supplier Payment", "", 9000 + i,
                         None, "", 100 + i) for i in range(25)]
    res = analyze_supplier(Supplier("B", 0, 999999, txns))
    assert res.bulk
    assert match_supplier(res, idx) == []
