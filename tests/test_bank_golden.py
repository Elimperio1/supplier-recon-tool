"""Golden numbers from the first real run (2026-09-21). Skips when the client files are absent.

The two exports are gitignored, so this only runs on a machine that has them.
"""

from datetime import date
from pathlib import Path

import pytest

from bankrecon.engine import TIER_AMOUNT, TIER_EXACT, TIER_SPLIT, Window, auto_window, reconcile
from bankrecon.parse import SIDE_CSV, SIDE_SAGE, parse_file

ROOT = Path(__file__).parent.parent
BANK = ROOT / "sa_bank_all_transactions (9).csv"
SAGE = ROOT / "BankTransactions (3) (OHC -7508 Sage Export.csv"

pytestmark = pytest.mark.skipif(not (BANK.exists() and SAGE.exists()),
                                reason="client exports not on this machine")


def test_golden_numbers():
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
    assert tiers.count(TIER_AMOUNT) == 1
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
