"""Gate a Streamlit tool behind a link minted by the practice-management app.

Drop this file next to the app's entrypoint, set SLUG, and call
`require_app_link()` as the first statement after `st.set_page_config(...)`.

Why this exists
---------------
These tools are public URLs on Streamlit Cloud: the URL *is* the access
control, so anyone who has ever clicked one keeps it after they leave the
firm. Instead of bolting an email and password onto each tool, the app mints a
120-second HMAC-signed link at the moment of the click, and this verifies it.
Nobody types anything; the click count does not change.

Design + threat model:
practice-management/docs/superpowers/specs/2026-09-22-tool-link-tokens-design.md

Secrets required (Streamlit Cloud -> app -> Settings -> Secrets):

    TOOL_LINK_SECRET = "the same value as the app's Vercel env"
    APP_ORIGIN = "https://practice-management-omega.vercel.app"

NEVER commit the real TOOL_LINK_SECRET. If this repo is public, a committed
secret lets anyone mint their own links and the whole gate is decorative.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import streamlit as st

# The tool this file is guarding. MUST match the slug in the app's
# src/lib/tools/menu.ts, because it is checked against the token's `aud`.
# One Streamlit app serving two menu entries (CSV Parse / Loan Reconciliation)
# needs the right SLUG per page, not one shared value.
SLUG = "recon"

# How long a verified entry lasts before a silent re-mint. Bounds how long a
# disabled account keeps working in a tab that is already open.
SESSION_SECONDS = 1800  # 30 minutes


def _b64d(s: str) -> bytes:
    """base64url-decode, restoring the padding the app strips when minting."""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _check(token: str) -> str:
    """Return 'ok', 'expired' or 'bad'.

    'expired' is the RECOVERABLE verdict and the only one that earns a bounce
    back to the app — it means the signature and audience were both good. A bad
    signature never bounces, because a mismatched secret would otherwise loop
    the browser between app and tool forever.
    """
    try:
        secret = st.secrets["TOOL_LINK_SECRET"].encode()
        payload, sig = token.split(".", 1)
        expected = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig)):
            return "bad"
        claims = json.loads(_b64d(payload))
        if claims.get("aud") != SLUG:
            return "bad"
        return "ok" if time.time() < float(claims["exp"]) else "expired"
    except Exception:
        # Malformed, truncated, wrong shape, missing secret — all indistinguishable
        # from tampering as far as this tool is concerned.
        return "bad"


def _bounce(url: str) -> None:
    """Send the browser back to the app to be re-minted.

    st.components.v1.html renders in an iframe, hence window.top. A <meta>
    refresh does NOT work here: Streamlit's markdown renderer strips meta tags,
    so the redirect would silently do nothing. The link is the no-JS fallback.
    """
    import streamlit.components.v1 as components

    components.html(
        f"<script>window.top.location.href = {json.dumps(url)};</script>",
        height=0,
    )
    st.info("Checking your sign-in…")
    st.link_button("Continue", url)
    st.stop()


def require_app_link() -> None:
    """Stop the script unless this session arrived through the app."""
    if st.session_state.get("_app_link_until", 0) > time.time():
        return

    qp = st.query_params
    token = qp.get("t")
    state = _check(token) if token else "expired"

    if state == "ok":
        st.session_state["_app_link_until"] = time.time() + SESSION_SECONDS
        # Clear ONLY our own parameters. st.query_params.clear() would wipe any
        # the tool itself uses.
        for key in ("t", "retry"):
            if key in qp:
                del qp[key]
        return

    # One free retry, and only for a token that was genuine but stale.
    if state == "expired" and qp.get("retry") != "1":
        _bounce(f"{st.secrets['APP_ORIGIN']}/tools/{SLUG}?retry=1")

    st.error(
        "Open this tool from the practice-management app "
        "(**Tools** in the top bar). Direct links do not work."
    )
    st.stop()
