"""Golden numbers from the real runs (2026-09-21). Skip when the client files are absent.

The exports are gitignored, so these only run on a machine that has them. Every
number below was measured on the real files, not assumed.
"""

from datetime import date
from pathlib import Path

import pytest

from bankrecon.engine import (TIER_AMOUNT, TIER_EXACT, TIER_NAME, TIER_SPLIT, Window,
                              auto_window, reconcile)
from bankrecon.parse import FORMAT_SECTIONED, SIDE_CSV, SIDE_SAGE, parse_file

ROOT = Path(__file__).parent.parent
BANK = ROOT / "sa_bank_all_transactions (9).csv"
SAGE = ROOT / "BankTransactions (3) (OHC -7508 Sage Export.csv"
REPORT = ROOT / "BanksAndCreditCardsTransactionsReport (11).csv"


@pytest.mark.skipif(not (BANK.exists() and SAGE.exists()), reason="client exports not on this machine")
def test_golden_bank_transactions_export():
    """Before Sage was corrected: five 27 Aug lines missing, one four-way split."""
    bank = parse_file(BANK.read_bytes(), SIDE_CSV)
    sage = parse_file(SAGE.read_bytes(), SIDE_SAGE)
    assert len(bank.txns) == 274 and bank.skipped == []
    assert len(sage.txns) == 1213 and sage.skipped == []

    assert auto_window(bank, sage) == Window(date(2026, 7, 31), date(2026, 8, 27))
    r = reconcile(bank, sage)

    assert len(r.csv_in) == 230
    assert len(r.sage_in) == 228
    assert len(r.sage_out) == 1213 - 228

    tiers = [m.tier for m in r.matches]
    assert tiers.count(TIER_EXACT) == 223
    assert tiers.count(TIER_NAME) == 1           # GOEIE HOOP OND11H29 vs GOEIE HOOP ON 11H29
    assert tiers.count(TIER_AMOUNT) == 0
    assert tiers.count(TIER_SPLIT) == 1
    split = next(m for m in r.matches if m.tier == TIER_SPLIT)
    assert split.csv_txns[0].cents == -1373003
    assert len(split.sage_txns) == 4
    assert {t.reference for t in split.sage_txns} == {"20260814-0006"}

    assert r.extra_in_sage == []
    assert [t.date for t in r.missing_in_sage] == [date(2026, 8, 27)] * 5
    assert r.missing_total == 4487570
    assert r.difference == 4487570
    assert r.proof_ok
    assert not r.sign_flip_hint
    assert r.sage_integrity_ok is None            # this export carries no balances


@pytest.mark.skipif(not (BANK.exists() and REPORT.exists()), reason="client exports not on this machine")
def test_golden_banks_and_credit_cards_report():
    """After Sage was corrected: the report and the bank CSV agree to the cent."""
    bank = parse_file(BANK.read_bytes(), SIDE_CSV)
    sage = parse_file(REPORT.read_bytes(), SIDE_SAGE)
    assert sage.format == FORMAT_SECTIONED
    assert sage.accounts == ["8400/000 : Standard Bank - 280687508"]
    assert len(sage.txns) == 481 and sage.skipped == []
    assert sage.opening == 5746760
    assert sage.closing == -1626251
    assert sage.integrity_ok is True

    assert auto_window(bank, sage) == Window(date(2026, 7, 31), date(2026, 9, 1))
    r = reconcile(bank, sage)
    assert len(r.csv_in) == 274 and r.csv_out == []
    assert len(r.sage_in) == 277

    tiers = [m.tier for m in r.matches]
    assert tiers.count(TIER_EXACT) == 209
    assert tiers.count(TIER_SPLIT) == 1
    split = next(m for m in r.matches if m.tier == TIER_SPLIT)
    assert len(split.sage_txns) == 4
    assert {t.reference for t in split.sage_txns} == {"20260814-0006"}
    assert {t.txn_type for t in split.sage_txns} == {"Account Payment", "Supplier Payment"}
    assert tiers.count(TIER_NAME) + tiers.count(TIER_AMOUNT) == 64
    assert tiers.count(TIER_AMOUNT) == 5          # amount-only pairs left for review

    assert r.missing_in_sage == []
    assert r.extra_in_sage == []
    assert r.difference == 0
    assert r.proof_ok
    assert not r.sign_flip_hint
    assert r.sage_integrity_ok is True
