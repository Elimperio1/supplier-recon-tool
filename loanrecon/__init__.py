"""Loan reconciliation engine: parse two ledger exports, match them, build the workbook.

Pure Python plus pandas/openpyxl, no Streamlit, so it can be tested on its own.
Money is integer cents end to end; display code divides by 100 at the edge.
"""
