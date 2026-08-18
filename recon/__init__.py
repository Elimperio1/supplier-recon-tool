"""Supplier Recon Tool - deterministic recon engine.

Pure-stdlib parsing/engine/match/aliases/export (no pandas, no Streamlit in this
package). The only heavy third-party deps live in the leaf modules that need them:
``sheets`` (gspread) and ``export`` (openpyxl). ``app.py`` is the only Streamlit file.
"""
