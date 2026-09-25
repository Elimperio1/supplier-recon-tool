"""House rules for every screen file, plus a smoke run of both tools with no uploads."""

import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCREEN_FILES = [ROOT / "app.py", ROOT / "ui.py",
                *sorted((ROOT / "tools").glob("*.py")),
                *sorted((ROOT / "bankrecon").glob("*.py")),
                *sorted((ROOT / "recon").glob("*.py"))]

BANNED_CHARS = re.compile("[–—️☀-➿\U0001f300-\U0001faff]")


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: p.name)
def test_no_dashes_or_emoji_in_screen_sources(path):
    text = path.read_text(encoding="utf-8")
    hit = BANNED_CHARS.search(text)
    assert hit is None, f"{path.name} contains {hit.group()!r} at offset {hit.start()}"


@pytest.mark.parametrize("path", [ROOT / "app.py", *sorted((ROOT / "tools").glob("*.py"))],
                         ids=lambda p: p.name)
def test_no_sidebar_and_no_page_icon(path):
    text = path.read_text(encoding="utf-8")
    assert "st.sidebar" not in text
    assert "page_icon" not in text


def test_only_the_entry_configures_the_page():
    for path in sorted((ROOT / "tools").glob("*.py")):
        assert "set_page_config" not in path.read_text(encoding="utf-8"), path.name


def _markdown(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_entry_renders_supplier_recon_by_default():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"))
    # Stand in for a session that already arrived through a signed app link.
    at.session_state["_app_link_until"] = time.time() + 60
    at.run(timeout=60)
    assert not at.exception
    assert "Recon Toolbox" in _markdown(at)
    assert "Upload a Supplier Transactions Report to begin" in _markdown(at)
    assert at.session_state["tool"] == "Supplier Recon"


def test_bank_recon_page_renders_empty_state():
    # AppTest.switch_page hashes the file name, st.Page hashes its url_path, so the
    # page is run directly here; the switcher itself is covered by the browser run.
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "tools" / "bank_recon.py"))
    at.run(timeout=60)
    assert not at.exception
    assert "Upload both files to compare" in _markdown(at)
