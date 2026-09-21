"""Recon Toolbox: entry point. Draws the shared frame, picks the tool, runs its page.

Streamlit's own navigation is hidden (no sidebar, house rule) and replaced by a
segmented control in the header row. The control's value is compared with the
page Streamlit resolved from the URL: a click that disagrees switches page, and
a URL that disagrees with a stale control value wins, so deep links still work.

Adding a tool is one line in TOOLS plus a page under ``tools/`` that renders
its inputs on the page, imports the design layer from ``ui.py``, does not set
the page config, and puts nothing in the sidebar.
"""

from __future__ import annotations

import streamlit as st

from ui import hero, inject_css

st.set_page_config(page_title="Recon Toolbox", layout="wide",
                   initial_sidebar_state="collapsed")

TOOLS = [
    st.Page("tools/supplier_recon.py", title="Supplier Recon", url_path="supplier", default=True),
    st.Page("tools/bank_recon.py", title="Bank Recon", url_path="bank"),
]
BY_TITLE = {page.title: page for page in TOOLS}

page = st.navigation(TOOLS, position="hidden")
inject_css()

current = page.title
chosen = st.session_state.get("tool")
if chosen in BY_TITLE and chosen != current and st.session_state.get("tool_page") == current:
    st.switch_page(BY_TITLE[chosen])
st.session_state["tool"] = current
st.session_state["tool_page"] = current

left, right = st.columns([3, 2], vertical_alignment="center")
with left:
    hero("Recon Toolbox")
right.segmented_control("Tool", list(BY_TITLE), key="tool",
                        label_visibility="collapsed", width="stretch")

page.run()
