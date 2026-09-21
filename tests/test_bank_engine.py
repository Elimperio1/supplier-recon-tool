from datetime import date
from pathlib import Path

import pytest

from bankrecon.engine import (STATUS_AMOUNT, STATUS_EXTRA, STATUS_MATCHED, STATUS_MISSING,
                          STATUS_OUTSIDE, STATUS_SPLIT, STATUS_DATE, TIER_AMOUNT, TIER_DATE,
                          TIER_EXACT, TIER_SPLIT, Window, auto_window, rand, reconcile)
from bankrecon.parse import SIDE_CSV, SIDE_SAGE, Parsed, Txn, parse_file

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def parsed():
    bank = parse_file((FIX / "bank_recon_bank.csv").read_bytes(), SIDE_CSV)
    sage = parse_file((FIX / "bank_recon_sage.csv").read_bytes(), SIDE_SAGE)
    return bank, sage


def test_auto_window_is_the_overlap(parsed):
    bank, sage = parsed
    assert auto_window(bank, sage) == Window(date(2026, 8, 1), date(2026, 8, 5))


def test_no_overlap():
    a = Parsed(SIDE_CSV, [Txn(SIDE_CSV, 2, date(2026, 1, 1), 100, "x")])
    b = Parsed(SIDE_SAGE, [Txn(SIDE_SAGE, 2, date(2026, 2, 1), 100, "x")])
    assert auto_window(a, b) is None
    with pytest.raises(ValueError):
        reconcile(a, b)


def test_fixture_reconciliation(parsed):
    bank, sage = parsed
    r = reconcile(bank, sage)

    assert len(r.csv_in) == 11 and len(r.csv_out) == 1
    assert len(r.sage_in) == 10 and len(r.sage_out) == 1
    assert r.csv_out[0].date == date(2026, 7, 30)
    assert r.sage_out[0].date == date(2026, 8, 10)

    tiers = [m.tier for m in r.matches]
    assert tiers.count(TIER_EXACT) == 4
    assert tiers.count(TIER_AMOUNT) == 1
    assert tiers.count(TIER_SPLIT) == 2
    assert tiers.count(TIER_DATE) == 0
    assert r.matched_csv_lines == 8
    assert r.matched_sage_lines == 8

    # Multiset: three fees against two leaves exactly one missing.
    missing = [(t.date, t.cents, t.description) for t in r.missing_in_sage]
    assert missing == [
        (date(2026, 8, 1), -15500, "0108 honouring fee"),
        (date(2026, 8, 3), 250000, "SALES DEPOSIT credit transfer"),
        (date(2026, 8, 5), 120000, "CUSTOMER ONE payment"),
    ]
    extra = [(t.date, t.cents) for t in r.extra_in_sage]
    assert extra == [(date(2026, 8, 4), 250000), (date(2026, 8, 5), -8000)]

    assert r.csv_total == -935500
    assert r.sage_total == -1048000
    assert r.difference == 112500
    assert r.missing_total == 354500
    assert r.extra_total == 242000
    assert r.proof_ok
    assert not r.sign_flip_hint


def test_amount_only_match_carries_a_note(parsed):
    r = reconcile(*parsed)
    m = next(m for m in r.matches if m.tier == TIER_AMOUNT)
    assert m.csv_txns[0].description == "PREPAID ELEC 12H00 purchase"
    assert m.sage_txns[0].description == "Electricity"
    assert "Descriptions differ" in m.note


def test_split_matches(parsed):
    r = reconcile(*parsed)
    splits = [m for m in r.matches if m.tier == TIER_SPLIT]
    sage_side = next(m for m in splits if len(m.sage_txns) == 2)
    assert sage_side.csv_txns[0].cents == -900000
    assert sorted(t.cents for t in sage_side.sage_txns) == [-600000, -300000]
    assert {t.reference for t in sage_side.sage_txns} == {"20260802-0003"}
    assert "20260802-0003" in sage_side.note
    csv_side = next(m for m in splits if len(m.csv_txns) == 2)
    assert csv_side.sage_txns[0].cents == -9000
    assert [t.cents for t in csv_side.csv_txns] == [-4500, -4500]


def test_date_tolerance_pairs_the_deposit(parsed):
    r = reconcile(*parsed, tolerance_days=1)
    m = next(m for m in r.matches if m.tier == TIER_DATE)
    assert m.csv_txns[0].date == date(2026, 8, 3)
    assert m.sage_txns[0].date == date(2026, 8, 4)
    assert m.note == "CSV dated 03/08/2026, Sage dated 04/08/2026."
    assert len(r.missing_in_sage) == 2
    assert len(r.extra_in_sage) == 1
    assert r.difference == 112500
    assert r.proof_ok


def test_date_tolerance_needs_the_same_description():
    a = Parsed(SIDE_CSV, [Txn(SIDE_CSV, 2, date(2026, 8, 3), 100, "alpha")])
    b = Parsed(SIDE_SAGE, [Txn(SIDE_SAGE, 2, date(2026, 8, 4), 100, "beta")])
    r = reconcile(a, b, Window(date(2026, 8, 1), date(2026, 8, 5)), tolerance_days=3)
    assert r.matches == []
    assert len(r.missing_in_sage) == 1 and len(r.extra_in_sage) == 1


def test_window_override(parsed):
    r = reconcile(*parsed, window=Window(date(2026, 8, 1), date(2026, 8, 2)))
    assert len(r.csv_in) == 7
    assert len(r.sage_in) == 7
    assert len(r.csv_out) == 5
    assert len(r.sage_out) == 4
    assert r.proof_ok


def test_status_map(parsed):
    r = reconcile(*parsed, tolerance_days=1)
    status = r.status_map()
    assert len(status) == len(r.csv_in) + len(r.csv_out) + len(r.sage_in) + len(r.sage_out)
    by_desc = {}
    for t in r.csv_in + r.csv_out + r.sage_in + r.sage_out:
        by_desc.setdefault((t.side, t.description), set()).add(status[t.key])
    assert by_desc[(SIDE_CSV, "OPENING CREDIT magtape credit")] == {STATUS_OUTSIDE}
    assert by_desc[(SIDE_CSV, "ACME HARDWARE ib payment")] == {STATUS_MATCHED}
    assert by_desc[(SIDE_CSV, "0108 honouring fee")] == {STATUS_MATCHED, STATUS_MISSING}
    assert by_desc[(SIDE_CSV, "PREPAID ELEC 12H00 purchase")] == {STATUS_AMOUNT}
    assert by_desc[(SIDE_CSV, "RENT AUGUST ib payment")] == {STATUS_SPLIT}
    assert by_desc[(SIDE_CSV, "SALES DEPOSIT credit transfer")] == {STATUS_DATE}
    assert by_desc[(SIDE_CSV, "CUSTOMER ONE payment")] == {STATUS_MISSING}
    assert by_desc[(SIDE_SAGE, "STATIONERY debit card purchase")] == {STATUS_EXTRA}
    assert by_desc[(SIDE_SAGE, "LATE LINE service fee")] == {STATUS_OUTSIDE}


def test_sign_flip_hint():
    when = date(2026, 8, 1)
    a = Parsed(SIDE_CSV, [Txn(SIDE_CSV, i, when, c, f"line {i}")
                          for i, c in enumerate([100, -200, 300], 2)])
    b = Parsed(SIDE_SAGE, [Txn(SIDE_SAGE, i, when, -c, f"line {i}")
                           for i, c in enumerate([100, -200, 300], 2)])
    r = reconcile(a, b)
    assert r.sign_flip_hint
    assert r.matches == []


def test_split_never_groups_sage_lines_without_a_reference():
    when = date(2026, 8, 1)
    a = Parsed(SIDE_CSV, [Txn(SIDE_CSV, 2, when, -200, "one payment")])
    b = Parsed(SIDE_SAGE, [Txn(SIDE_SAGE, 2, when, -150, "fee"), Txn(SIDE_SAGE, 3, when, -50, "fee")])
    r = reconcile(a, b)
    assert r.matches == []
    b = Parsed(SIDE_SAGE, [Txn(SIDE_SAGE, 2, when, -150, "fee", reference="R1"),
                           Txn(SIDE_SAGE, 3, when, -50, "fee", reference="R1")])
    r = reconcile(a, b)
    assert [m.tier for m in r.matches] == [TIER_SPLIT]


def test_reverse_split_needs_the_same_sign():
    when = date(2026, 8, 1)
    a = Parsed(SIDE_CSV, [Txn(SIDE_CSV, 2, when, 300, "fee"), Txn(SIDE_CSV, 3, when, -100, "fee")])
    b = Parsed(SIDE_SAGE, [Txn(SIDE_SAGE, 2, when, 200, "fee")])
    r = reconcile(a, b)
    assert r.matches == []


def test_exact_prefers_description_when_amounts_tie():
    when = date(2026, 8, 1)
    a = Parsed(SIDE_CSV, [Txn(SIDE_CSV, 2, when, -500, "VODACOM debit transfer")])
    b = Parsed(SIDE_SAGE, [
        Txn(SIDE_SAGE, 2, when, -500, "Telkom", comment="TELKOM debit transfer"),
        Txn(SIDE_SAGE, 3, when, -500, "Vodacom", comment="VODACOM debit transfer"),
    ])
    r = reconcile(a, b)
    assert r.matches[0].tier == TIER_EXACT
    assert r.matches[0].sage_txns[0].row == 3


def test_amount_only_prefers_most_words_in_common():
    when = date(2026, 8, 1)
    a = Parsed(SIDE_CSV, [Txn(SIDE_CSV, 2, when, -500, "GOEIE HOOP OND11H29 debit card purchase")])
    b = Parsed(SIDE_SAGE, [
        Txn(SIDE_SAGE, 2, when, -500, "Groceries"),
        Txn(SIDE_SAGE, 3, when, -500, "GOEIE HOOP ON 11H29 debit card purchase"),
    ])
    r = reconcile(a, b)
    assert r.matches[0].tier == TIER_AMOUNT
    assert r.matches[0].sage_txns[0].row == 3


def test_rand_formats_cents_without_floats():
    assert rand(4487570) == "44,875.70"
    assert rand(-1) == "-0.01"
    assert rand(0) == "0.00"
    assert rand(None) == ""
