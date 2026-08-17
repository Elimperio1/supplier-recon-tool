"""Parser trap coverage (BUILD.md §2, §8)."""

from pathlib import Path

import pytest

from recon.parse import (parse_bank_report, parse_supplier_report, to_cents)

FIX = Path(__file__).parent / "fixtures"
SUP = FIX / "supplier_traps.csv"
BANK = FIX / "bank_basic.csv"


# -- to_cents ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1234.56", 123456),
    ("1,234.56", 123456),          # thousands separator
    ("0", 0),
    ("", None),
    ("   ", None),
    ("R 1 000.00", 100000),        # currency symbol + spaces
    ("(123.45)", -12345),          # parenthesised negative
    ("-50.00", -5000),
    ("1369.28", 136928),           # cent-exact, no float drift
])
def test_to_cents(raw, expected):
    assert to_cents(raw) == expected


# -- supplier report --------------------------------------------------------

def test_sep_line_and_header_skipped():
    r = parse_supplier_report(SUP)
    names = [s.name for s in r.suppliers]
    assert "sep=" not in names
    assert not any(n.startswith("Supplier") for n in names)
    assert len(r.suppliers) == 7


def test_column_trap_both_sides():
    r = parse_supplier_report(SUP)
    by = {s.name: s for s in r.suppliers}
    assert by["Alpha Credit Supplier"].closing == 100000   # credit column
    assert by["Beta Debit Supplier"].closing == -30000     # debit column (negative)


def test_zero_txn_nonzero_opening():
    r = parse_supplier_report(SUP)
    g = next(s for s in r.suppliers if s.name == "Gamma Opening Only")
    assert g.opening == 10000
    assert g.txns == []
    assert g.closing == 10000


def test_empty_and_date_shaped_descriptions_are_transactions():
    r = parse_supplier_report(SUP)
    d = next(s for s in r.suppliers if s.name == "Delta Combo")
    descs = [t.description for t in d.txns]
    assert "" in descs                 # empty description still a txn
    assert "16/03/2026" in descs       # date-shaped description still a txn
    assert len(d.txns) == 3


def test_movement_and_grand_total_ignored():
    r = parse_supplier_report(SUP)
    # No txn carries a Movement/Grand Total marker, and none was miscounted.
    for s in r.suppliers:
        for t in s.txns:
            assert not t.date.startswith("Movement")
            assert not t.date.startswith("Grand Total")


def test_unrecognized_row_surfaced_not_dropped():
    r = parse_supplier_report(SUP)
    assert len(r.unrecognized) == 1
    assert r.unrecognized[0][1][0].startswith("Note:")


def test_integrity_recompute_matches_reported():
    r = parse_supplier_report(SUP)
    assert r.integrity_failures == []
    for s in r.suppliers:
        assert s.recomputed_closing == s.closing


def test_money_is_integer_cents():
    r = parse_supplier_report(SUP)
    a = next(s for s in r.suppliers if s.name == "Alpha Credit Supplier")
    for t in a.txns:
        assert t.debit is None or isinstance(t.debit, int)
        assert t.credit is None or isinstance(t.credit, int)


# -- bank report ------------------------------------------------------------

def test_bank_inverted_semantics_amount_out_is_credit():
    b = parse_bank_report(BANK)
    pnp = next(t for t in b.all_txns if "PnP" in t.description)
    assert pnp.credit == 15798          # money out lives in Credit
    assert pnp.amount_out == 15798
    assert pnp.debit is None


def test_bank_three_accounts_grand_total_not_a_section():
    b = parse_bank_report(BANK)
    assert len(b.accounts) == 1
    assert b.accounts[0].name.startswith("8400/000")


def test_same_batch_ref_multiple_rows_distinct():
    b = parse_bank_report(BANK)
    split = [t for t in b.all_txns if t.reference == "20260102-0003"]
    assert len(split) == 3
    assert len({t.row_index for t in split}) == 3      # identity = row, not ref
    assert sorted(t.amount_out for t in split) == [5000, 10000, 20000]


def test_bank_integrity_ok():
    b = parse_bank_report(BANK)
    assert b.integrity_failures == []
    assert b.accounts[0].closing == 20000              # 200.00 debit balance


# -- duplicate supplier names (§8) ------------------------------------------

def test_duplicate_supplier_names_kept_separate_and_warned():
    data = (
        "﻿sep=,\r\n"
        "Supplier,Reference,Transaction Type,Description,Debit,Credit,Balance\r\n"
        "Dup Co,,,,,,\r\n"
        "Opening Balance as at:  01/03/2026,,,,,0,\r\n"
        "01/03/2026,SIV1,Supplier Invoice,a,,10.00,10.00\r\n"
        "Closing Balance as at:  28/02/2027,,,,,10.00,\r\n"
        "Dup Co,,,,,,\r\n"
        "Opening Balance as at:  01/03/2026,,,,,0,\r\n"
        "01/03/2026,SIV2,Supplier Invoice,b,,20.00,20.00\r\n"
        "Closing Balance as at:  28/02/2027,,,,,20.00,\r\n"
    ).encode("utf-8")
    r = parse_supplier_report(data)
    dups = [s for s in r.suppliers if s.name == "Dup Co"]
    assert len(dups) == 2                          # kept separate, not merged
    assert {s.closing for s in dups} == {1000, 2000}
    assert all(s.duplicate for s in dups)
    assert "Dup Co" in r.duplicate_names
