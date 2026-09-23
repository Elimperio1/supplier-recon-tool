from datetime import date
from pathlib import Path

import pytest

from bankrecon.parse import (FORMAT_SECTIONED, SIDE_CSV, SIDE_SAGE, ParseError, header_name,
                             parse_cents, parse_date, parse_file)

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("text,cents", [
    ("-1693.75", -169375),
    ("-29727.9000", -2972790),
    ("13598.75", 1359875),
    ("24916.7", 2491670),
    ("1,234.56", 123456),
    ("-1,234.56", -123456),
    ("(12.00)", -1200),
    ("12.00-", -1200),
    ("R 5.00", 500),
    ("1 234,56", 123456),
    ("12,34", 1234),
    ("1,234", 123400),
    ("0.005", 1),
    ("0", 0),
    ("", None),
    ("abc", None),
])
def test_parse_cents(text, cents):
    assert parse_cents(text) == cents


@pytest.mark.parametrize("text,when", [
    ("27/08/2026", date(2026, 8, 27)),
    ("2026-08-27", date(2026, 8, 27)),
    ("27-08-2026", date(2026, 8, 27)),
    ("27/08/26", date(2026, 8, 27)),
    (" 01/03/2026 ", date(2026, 3, 1)),
    ("Total", None),
    ("", None),
    ("31/02/2026", None),
])
def test_parse_date(text, when):
    assert parse_date(text) == when


def test_bank_csv_with_meta_block():
    parsed = parse_file((FIX / "bank_recon_bank.csv").read_bytes(), SIDE_CSV)
    assert parsed.preamble_rows == 4
    assert parsed.amount_column == "Amount"
    assert parsed.header == ["Date", "Description", "Amount"]
    assert len(parsed.txns) == 12
    assert parsed.skipped == []
    first = parsed.txns[0]
    assert first.row == 6                      # 1-based line in the file
    assert first.date == date(2026, 7, 30)
    assert first.cents == 10000
    assert first.description == "OPENING CREDIT magtape credit"
    assert first.side == SIDE_CSV
    assert parsed.first_date == date(2026, 7, 30)
    assert parsed.last_date == date(2026, 8, 5)


def test_sage_export_reads_total_and_extra_columns():
    parsed = parse_file((FIX / "bank_recon_sage.csv").read_bytes(), SIDE_SAGE)
    assert parsed.preamble_rows == 0
    assert parsed.amount_column == "Total"
    assert len(parsed.txns) == 11
    fee = parsed.txns[2]
    assert fee.row == 4
    assert fee.cents == -15500                  # Total, not Exclusive
    assert fee.description == "0108 honouring fee"
    assert fee.comment == "0108 honouring fee"
    assert fee.reference == "20260801-0003"
    assert fee.txn_type == "Account"
    assert fee.selection == "Bank Charges"
    assert fee.reconciled == "True"
    assert parsed.txns[3].comment == ""


def test_unreadable_rows_are_reported_not_dropped():
    data = (
        "Date,Description,Amount\n"
        "01/08/2026,ok line,-10.00\n"
        "\n"
        "Total,,-10.00\n"
        "02/08/2026,bad amount,ten\n"
        "03/08/2026,also ok,5\n"
    ).encode()
    parsed = parse_file(data, SIDE_CSV)
    assert [t.cents for t in parsed.txns] == [-1000, 500]
    assert parsed.skipped == [
        (4, "date not recognised: 'Total'"),
        (5, "amount not recognised: 'ten'"),
    ]


def test_details_column_and_bom_and_cp1252():
    data = b"\xef\xbb\xbfDate,Details,Amount\n01/08/2026,CAF\xc9 purchase,-20.00\n"
    parsed = parse_file(data, SIDE_CSV)
    assert len(parsed.txns) == 1
    assert parsed.txns[0].description.startswith("CAF")
    assert parsed.txns[0].cents == -2000


def test_no_header_raises():
    with pytest.raises(ParseError):
        parse_file(b"a,b,c\n1,2,3\n", SIDE_CSV)


def test_header_name_takes_the_last_line_of_a_two_line_cell():
    assert header_name("Bank Account \n                            Date") == "date"
    assert header_name("Account / Customer / Supplier") == "account / customer / supplier"
    assert header_name("  Transaction   Type ") == "transaction type"
    assert header_name("") == ""


def test_sectioned_report_first_account_by_default():
    parsed = parse_file((FIX / "bank_recon_sage_report.csv").read_bytes(), SIDE_SAGE)
    assert parsed.format == FORMAT_SECTIONED
    assert parsed.amount_column == "Debit minus Credit"
    assert parsed.accounts == ["8400/000 : Test Bank - 1234", "8500/000 : Petty Cash"]
    assert parsed.account == "8400/000 : Test Bank - 1234"
    assert parsed.skipped == []
    assert len(parsed.txns) == 11
    assert parsed.opening == 100000
    assert parsed.closing == -957900
    assert parsed.movement == -1057900
    assert parsed.integrity_ok is True

    first = parsed.txns[0]
    assert first.row == 5
    assert first.date == date(2026, 8, 1)
    assert first.cents == -150000                      # Credit column is money out
    assert first.description == "ACME HARDWARE ib payment"
    assert first.comment == "Repairs"
    assert first.reference == "20260801-0001"
    assert first.txn_type == "Account Payment"
    assert first.selection == "8400/000 : Test Bank - 1234"

    supplier = parsed.txns[1]                          # batch ref in Description, name in Account
    assert supplier.description == "20260801-0002"
    assert supplier.reference == "20260801-0002"       # the batch ref, not PAY0000001
    assert supplier.comment == "Acme Hardware"
    assert supplier.txn_type == "Supplier Payment"

    receipt = next(t for t in parsed.txns if t.txn_type == "Customer Receipt")
    assert receipt.cents == 250000                     # Debit column is money in
    assert receipt.comment == "Sales Customer"


def test_sectioned_report_chosen_account():
    parsed = parse_file((FIX / "bank_recon_sage_report.csv").read_bytes(), SIDE_SAGE,
                        account="8500/000 : Petty Cash")
    assert parsed.account == "8500/000 : Petty Cash"
    assert [t.cents for t in parsed.txns] == [30000]
    assert parsed.opening == 20000 and parsed.closing == 50000
    assert parsed.integrity_ok is True
    unknown = parse_file((FIX / "bank_recon_sage_report.csv").read_bytes(), SIDE_SAGE, account="nope")
    assert unknown.account == "8400/000 : Test Bank - 1234"


def test_sectioned_report_integrity_fails_when_a_line_is_missing():
    text = (FIX / "bank_recon_sage_report.csv").read_text(encoding="utf-8")
    broken = text.replace('"05/08/2026",,"STATIONERY debit card purchase","20260805-0001",'
                          '"Account Payment","Stationery",,"80","-9480"\n', "")
    parsed = parse_file(broken.encode(), SIDE_SAGE)
    assert len(parsed.txns) == 10
    assert parsed.integrity_ok is False


def test_sectioned_report_reports_odd_rows():
    data = (
        "sep=,\n"
        '"Bank Account \nDate","Payee","Description","Reference","Transaction Type",'
        '"Account / Customer / Supplier","Debit","Credit","Balance"\n'
        '"01/08/2026",,"before any header","R1","Account Payment","X",,"5","-5"\n'
        '"8400/000 : Bank",,,,,,,,\n'
        '"01/08/2026",,"no amount","R2","Account Payment","X",,,"-5"\n'
        '"Some stray text",,"x",,,,,,\n'
        '"02/08/2026",,"fine","R3","Account Payment","X","10",,"5"\n'
    ).encode()
    parsed = parse_file(data, SIDE_SAGE)
    assert [t.cents for t in parsed.txns] == [1000]
    assert parsed.skipped == [
        (3, "dated row above the first bank account header"),
        (5, "no debit or credit amount"),
        (6, "row not recognised: 'Some stray text'"),
    ]
    assert parsed.integrity_ok is None                 # no balances in this file
