"""Workbook rendering (§8). The Excel file is a second renderer over the same
engine result, so the things worth asserting are the ones only it produces:
the per-account Totals band and the amber aging flag on the description."""

from recon.engine import analyze
from recon.export import AMBER_FILL, build_workbook
from recon.parse import Supplier, SupplierReport, SupplierTxn, without_months


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
    # Paid Co: an April payment with no invoice.
    # Odd Co: a row whose date does not read, so no month can exclude it.
    report = SupplierReport(suppliers=[
        Supplier("Settles Co", 0, 3000, [
            _inv("Settles Co", "I1", 5000, "02/03/2026", 0),
            _pay("Settles Co", "P1", 5000, "09/03/2026", 1),
            _inv("Settles Co", "I2", 3000, "05/04/2026", 2, desc="april invoice")]),
        Supplier("Late Co", 0, 5000, [
            _inv("Late Co", "I1", 4000, "10/02/2026", 3, desc="february invoice"),
            _inv("Late Co", "I2", 1000, "11/04/2026", 4, desc="april invoice")]),
        Supplier("Paid Co", 0, -2000, [
            _pay("Paid Co", "P1", 2000, "15/04/2026", 5, desc="april payment")]),
        Supplier("Odd Co", 0, 2000, [_inv("Odd Co", "I1", 2000, "n/a", 6, desc="undated")])])
    report = without_months(report, months)
    return build_workbook("Client", analyze(report), {}, report, exclude_months=months)


def test_excluded_months_leave_both_ledger_sheets():
    for sheet in ("Ledger", "Non-zero Ledger"):
        descs = {r[4] for r in _rows(_excluding(["2026-04"])[sheet])}
        assert not {"april invoice", "april payment"} & descs, sheet
        assert {"february invoice", "undated"} <= descs, sheet

    wb = _excluding(["2026-04"])
    label = "Closing balance excl. Apr 2026"
    closing = {r[0]: r[7] for r in _rows(wb["Ledger"]) if r[4] == label}
    assert closing == {"Settles Co": 0.00, "Late Co": 40.00, "Paid Co": 0.00, "Odd Co": 20.00}
    # Settles Co and Paid Co are R0 once April is out.
    assert _names(wb["Non-zero Ledger"]) == ["Late Co", "Odd Co"]
    totals = [(r[5], r[6]) for r in _rows(wb["Non-zero Ledger"]) if r[4] == "Totals"]
    assert totals == [(0.00, 40.00), (0.00, 20.00)]


def test_excluded_months_leave_the_other_sheets():
    def summary(wb):
        return {r[0]: (r[1], r[2]) for r in _rows(wb["Summary"])[1:]}

    def invoices_needed(wb):
        return [r[0] for r in _rows(wb["Invoices Needed"])[1:]]

    full, cut = _excluding([]), _excluding(["2026-04"])
    assert summary(full)["Settles Co"] == (30.00, "PAYMENTS NEEDED")
    assert summary(cut)["Settles Co"] == (0.00, "GREEN")
    assert invoices_needed(full) == ["Paid Co"]
    assert invoices_needed(cut) == []


def test_no_months_excluded_keeps_the_plain_closing_label():
    labels = {r[4] for r in _rows(_excluding([])["Ledger"])}
    assert "Closing balance" in labels
    assert not any(str(x).startswith("Closing balance excl.") for x in labels)


def test_payments_needed_shows_the_invoice_description():
    # The description holds the supplier's own invoice number; the Sage SIV ref
    # stays in its own column beside it.
    from recon.match import account_payment_index, match_supplier
    from recon.parse import BankAccount, BankReport, BankTxn

    report = SupplierReport(suppliers=[Supplier("Owed Co", 0, 5000, [
        _inv("Owed Co", "SIV0042", 5000, "01/06/2026", 0, desc="INV 88231")])])
    engine = analyze(report)
    bank = BankReport(accounts=[BankAccount("FNB", 0, -5000, [
        BankTxn("FNB", 0, "05/06/2026", "", "OWED CO", "B1",
                "Account Payment", "Sundry", None, 5000, "")], -5000)])
    index = account_payment_index(bank)
    matches = {r.name: match_supplier(r, index) for r in engine.suppliers}
    ws = build_workbook("Client", engine, matches, report, bank)["Payments Needed"]

    rows = [[c.value for c in row] for row in ws.iter_rows(min_row=1)]
    col = {h: i for i, h in enumerate(rows[0])}
    assert rows[0][:4] == ["Supplier", "Invoice Ref", "Invoice Description", "Invoice Date"]
    inv = rows[1]
    assert inv[col["Invoice Ref"]] == "SIV0042"
    assert inv[col["Invoice Description"]] == "INV 88231"
    assert inv[col["Invoice Date"]] == "01/06/2026"
    assert inv[col["Amount (R)"]] == 50.00
    assert inv[col["Bank Description"]] == "OWED CO"


def test_ledger_totals_band_emitted_per_supplier():
    a = Supplier("A Co", 0, 5000, [_inv("A Co", "I1", 5000, "01/06/2026", 0)])
    b = Supplier("B Co", 0, -2000, [_pay("B Co", "P1", 2000, "01/06/2026", 1)])
    ws = _ledger([a, b])
    totals = [r for r in _rows(ws) if r[4] == "Totals"]
    assert [(t[5], t[6]) for t in totals] == [(0.00, 50.00), (20.00, 0.00)]
