"""Workbook rendering (§8). The Excel file is a second renderer over the same
engine result, so the things worth asserting are the ones only it produces:
the per-account Totals band and the amber aging flag on the description."""

from recon.engine import analyze
from recon.export import AMBER_FILL, build_workbook
from recon.parse import Supplier, SupplierReport, SupplierTxn


def _inv(name, ref, cents, date, ri, desc="", bal=""):
    return SupplierTxn(name, date, ref, "Supplier Invoice", desc, None, cents, bal, ri)


def _pay(name, ref, cents, date, ri, desc="", bal=""):
    return SupplierTxn(name, date, ref, "Supplier Payment", desc, cents, None, bal, ri)


def _ledger(suppliers):
    report = SupplierReport(suppliers=suppliers)
    wb = build_workbook("Client", analyze(report), {}, report)
    return wb["Ledger"]


def _rows(ws):
    return [[c.value for c in row] for row in ws.iter_rows(min_row=1, max_col=10)]


def test_ledger_totals_band_foots_debits_and_credits():
    # Opening 0 + credits 180.00 - debits 50.00 = closing 130.00.
    s = Supplier("Foot Co", 0, 13000, [
        _inv("Foot Co", "I1", 5000, "01/06/2026", 0),
        _inv("Foot Co", "I2", 13000, "02/06/2026", 1),
        _pay("Foot Co", "P1", 5000, "03/06/2026", 2)])
    ws = _ledger([s])
    totals = [r for r in _rows(ws) if r[4] == "Totals"]
    assert len(totals) == 1
    assert (totals[0][5], totals[0][6]) == (50.00, 180.00)   # Debit (R), Credit (R)

    # It sits under the transactions and above the closing balance band.
    labels = [r[4] for r in _rows(ws)]
    assert labels.index("Totals") < labels.index("Closing balance")
    assert labels.index("Opening balance") < labels.index("Totals")


def test_ledger_description_is_amber_only_when_over_thirty_days():
    # One pair 2 days apart (clean) and one 141 days apart (aged). Both stay
    # matched - only the aged row's description cell is painted.
    s = Supplier("Slow Co", 0, 0, [
        _inv("Slow Co", "I1", 5000, "01/06/2026", 0, desc="quick invoice"),
        _pay("Slow Co", "P1", 5000, "03/06/2026", 1, desc="quick payment"),
        _inv("Slow Co", "I2", 9000, "01/03/2026", 2, desc="slow invoice"),
        _pay("Slow Co", "P2", 9000, "20/07/2026", 3, desc="slow payment")])
    ws = _ledger([s])
    amber = AMBER_FILL.fgColor.rgb
    painted = {}
    for row in ws.iter_rows(min_row=1, max_col=9):
        desc = row[4]
        if desc.value in ("quick invoice", "quick payment", "slow invoice", "slow payment"):
            painted[desc.value] = desc.fill.fgColor.rgb == amber
    assert painted == {"quick invoice": False, "quick payment": False,
                       "slow invoice": True, "slow payment": True}


def test_ledger_keeps_the_reports_own_running_balance():
    # The Balance column is the source report's, never recomputed: it is written
    # verbatim (as a number), and the opening/closing bands land in that same
    # column so the balance reads as one continuous run down the sheet.
    s = Supplier("Run Co", 0, 0, [
        _pay("Run Co", "P1", 5120, "28/07/2026", 0, desc="ib payment", bal="-51.20"),
        _inv("Run Co", "I1", 5120, "28/07/2026", 1, desc="invoice", bal="0")])
    ws = _ledger([s])
    assert [c.value for c in ws[1]][:8][-1] == "Balance (R)"
    body = [r for r in _rows(ws) if r[1] in ("28/07/2026",)]
    assert [r[7] for r in body] == [-51.20, 0.00]

    labels = {r[4]: r[7] for r in _rows(ws) if r[4] in ("Opening balance", "Closing balance")}
    assert labels == {"Opening balance": 0.00, "Closing balance": 0.00}


def test_ledger_unreadable_balance_cell_is_kept_as_text_not_dropped():
    s = Supplier("Odd Co", 0, 5000, [
        _inv("Odd Co", "I1", 5000, "01/06/2026", 0, bal="see attached")])
    ws = _ledger([s])
    body = [r for r in _rows(ws) if r[1] == "01/06/2026"]
    assert body[0][7] == "see attached"


def _names(ws):
    return [r[0] for r in _rows(ws) if r[4] == "Opening balance"]


def test_non_zero_ledger_holds_only_accounts_not_closing_at_zero():
    settled = Supplier("Zero Co", 0, 0, [
        _inv("Zero Co", "I1", 5000, "01/06/2026", 0),
        _pay("Zero Co", "P1", 5000, "02/06/2026", 1)])
    owed = Supplier("Owed Co", 0, 5000, [_inv("Owed Co", "I1", 5000, "01/06/2026", 2)])
    overpaid = Supplier("Over Co", 0, -2000, [_pay("Over Co", "P1", 2000, "01/06/2026", 3)])
    report = SupplierReport(suppliers=[settled, owed, overpaid])
    wb = build_workbook("Client", analyze(report), {}, report)

    assert wb.sheetnames.index("Non-zero Ledger") == wb.sheetnames.index("Ledger") + 1
    assert _names(wb["Ledger"]) == ["Zero Co", "Owed Co", "Over Co"]
    assert _names(wb["Non-zero Ledger"]) == ["Owed Co", "Over Co"]
    # Same layout as the full Ledger, account for account.
    full = _rows(wb["Ledger"])
    start = next(i for i, r in enumerate(full) if r[0] == "Owed Co")
    assert _rows(wb["Non-zero Ledger"]) == [full[0]] + full[start:]


def _excluding(months):
    # Settles Co: March nets to zero, April invoice still open.
    # Late Co: February invoice still open, April invoice on top.
    # Odd Co: a row whose date does not parse, so no month can exclude it.
    settles = Supplier("Settles Co", 0, 3000, [
        _inv("Settles Co", "I1", 5000, "02/03/2026", 0),
        _pay("Settles Co", "P1", 5000, "09/03/2026", 1),
        _inv("Settles Co", "I2", 3000, "05/04/2026", 2, desc="april invoice")])
    late = Supplier("Late Co", 0, 5000, [
        _inv("Late Co", "I1", 4000, "10/02/2026", 3, desc="february invoice"),
        _inv("Late Co", "I2", 1000, "11/04/2026", 4, desc="april invoice")])
    odd = Supplier("Odd Co", 0, 2000, [_inv("Odd Co", "I1", 2000, "n/a", 5, desc="undated")])
    report = SupplierReport(suppliers=[settles, late, odd])
    return build_workbook("Client", analyze(report), {}, report, exclude_months=months)


def test_excluded_month_drops_rows_and_retests_the_balance():
    ws = _excluding(["2026-04"])["Non-zero Ledger"]
    rows = _rows(ws)
    # Settles Co is R0 once April is out, so it leaves the sheet.
    assert _names(ws) == ["Late Co", "Odd Co"]
    descs = [r[4] for r in rows]
    assert "april invoice" not in descs
    assert {"february invoice", "undated"} <= set(descs)
    closing = {r[0]: r[7] for r in rows if r[4] == "Closing balance excl. Apr 2026"}
    assert closing == {"Late Co": 40.00, "Odd Co": 20.00}
    totals = [(r[5], r[6]) for r in rows if r[4] == "Totals"]
    assert totals == [(0.00, 40.00), (0.00, 20.00)]


def test_excluding_months_leaves_the_full_ledger_alone():
    assert _rows(_excluding(["2026-04"])["Ledger"]) == _rows(_excluding([])["Ledger"])
    assert _names(_excluding([])["Non-zero Ledger"]) == ["Settles Co", "Late Co", "Odd Co"]


def test_ledger_months_lists_dated_months_oldest_first():
    from recon.export import ledger_months, month_label

    report = SupplierReport(suppliers=[
        Supplier("A Co", 0, 0, [_inv("A Co", "I1", 100, "05/04/2026", 0),
                                _pay("A Co", "P1", 100, "n/a", 1)]),
        Supplier("B Co", 0, 0, [_inv("B Co", "I1", 100, "28/12/2025", 2)])])
    months = ledger_months(analyze(report))
    assert months == ["2025-12", "2026-04"]
    assert [month_label(m) for m in months] == ["Dec 2025", "Apr 2026"]


def test_ledger_totals_band_emitted_per_supplier():
    a = Supplier("A Co", 0, 5000, [_inv("A Co", "I1", 5000, "01/06/2026", 0)])
    b = Supplier("B Co", 0, -2000, [_pay("B Co", "P1", 2000, "01/06/2026", 1)])
    ws = _ledger([a, b])
    totals = [r for r in _rows(ws) if r[4] == "Totals"]
    assert [(t[5], t[6]) for t in totals] == [(0.00, 50.00), (20.00, 0.00)]
