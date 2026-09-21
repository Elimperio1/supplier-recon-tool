"""Shared design layer for the Recon Toolbox pages.

The look is a locked light "Apple" aesthetic: system typography, translucent
material cards, a segmented-control tab bar, stat tiles, press feedback, and all
default Streamlit chrome (menu, footer, header, sidebar) hidden. No sidebar.
Selectors use the durable hooks first: data-testid, then ARIA roles.
"""

from __future__ import annotations

import html

import streamlit as st

CSS = """
:root{
  --bg:#f5f5f7; --surface:rgba(255,255,255,.72); --solid:#fff;
  --border:rgba(0,0,0,.08); --hair:rgba(0,0,0,.06);
  --ink:#1d1d1f; --ink2:#6e6e73; --ink3:#86868b;
  --accent:#0071e3; --accent-press:#0060c0;
  --g-fg:#0a7d33; --g-bg:rgba(52,199,89,.14);
  --r-fg:#c1121f; --r-bg:rgba(255,59,48,.11);
  --a-fg:#8a6100; --a-bg:rgba(255,179,64,.17);
  --shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px rgba(0,0,0,.05);
  --radius:16px; --sys:-apple-system,BlinkMacSystemFont,"SF Pro Text","SF Pro Display","Inter","Segoe UI",Roboto,system-ui,sans-serif;
}
/* hide all default Streamlit chrome */
#MainMenu,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],
header[data-testid="stHeader"],footer,[data-testid="stSidebar"],[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"]{display:none!important;}

.stApp{background:var(--bg);}
.stApp,.stApp p,.stApp label,.stApp span,.stApp div,.stApp input,.stApp button,
.stMarkdown,h1,h2,h3,h4{font-family:var(--sys);}
/* keep icon fonts intact */
.material-icons,.material-icons-outlined,[class*="material-symbols"],
[data-testid="stIconMaterial"]{font-family:"Material Symbols Rounded","Material Symbols Outlined","Material Icons"!important;}

.block-container{max-width:1180px;padding-top:1.6rem;padding-bottom:4rem;}

/* hero ------------------------------------------------------------------ */
.hero{display:flex;align-items:center;justify-content:space-between;gap:16px;
  margin:.2rem 0 1.4rem;}
.hero__title{font-size:2.05rem;font-weight:640;letter-spacing:-.03em;color:var(--ink);
  line-height:1.05;}

/* stat tiles ------------------------------------------------------------ */
.tiles{display:grid;grid-template-columns:repeat(var(--n,5),1fr);gap:14px;margin:.2rem 0 1.1rem;}
.tile{background:var(--surface);backdrop-filter:blur(20px) saturate(180%);
  -webkit-backdrop-filter:blur(20px) saturate(180%);border:1px solid var(--border);
  border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow);}
.tile__v{font-size:1.9rem;font-weight:640;letter-spacing:-.03em;color:var(--ink);line-height:1;}
.tile__l{margin-top:.5rem;font-size:.72rem;font-weight:600;letter-spacing:.055em;
  text-transform:uppercase;color:var(--ink3);}
.tile--green .tile__v{color:var(--g-fg);} .tile--red .tile__v{color:var(--r-fg);}
.tile--amber .tile__v{color:var(--a-fg);}
@media(max-width:900px){.tiles{grid-template-columns:repeat(2,1fr);}}

/* section label + inline verdict pills ---------------------------------- */
.sec{font-size:.9rem;color:var(--ink2);letter-spacing:-.01em;margin:.1rem 0 .9rem;}
.vp{display:inline-flex;align-items:center;gap:.35rem;padding:.16rem .6rem;border-radius:980px;
  font-size:.78rem;font-weight:600;letter-spacing:-.01em;}
.vp--green{color:var(--g-fg);background:var(--g-bg);}
.vp--amber{color:var(--a-fg);background:var(--a-bg);}
.vp--red{color:var(--r-fg);background:var(--r-bg);}
.inv{font-weight:600;color:var(--ink);letter-spacing:-.01em;}

/* tabs -> segmented control (Streamlit 1.61: [role=tablist] / [data-testid=stTab]) */
.stTabs [role="tablist"]{gap:4px;background:rgba(0,0,0,.05);padding:5px;border:none!important;
  border-radius:13px;display:inline-flex;flex-wrap:wrap;margin-bottom:.4rem;}
.stTabs [role="tablist"]::after,.stTabs [role="tablist"]::before{display:none!important;}
.stTabs [data-testid="stTab"]{height:auto;padding:.48rem 1.05rem;border-radius:9px;color:var(--ink2);
  font-weight:560;letter-spacing:-.01em;border:none!important;background:transparent;
  transition:color .15s ease,background .15s ease,box-shadow .15s ease;}
.stTabs [data-testid="stTab"]:hover{color:var(--ink);}
.stTabs [data-testid="stTab"][aria-selected="true"]{background:var(--solid);color:var(--ink)!important;
  box-shadow:0 1px 3px rgba(0,0,0,.14);}
.stTabs [data-testid="stTab"] p{font-weight:560;letter-spacing:-.01em;}

/* buttons --------------------------------------------------------------- */
.stButton>button,.stDownloadButton>button{border-radius:980px;border:1px solid var(--border);
  background:var(--solid);color:var(--ink);font-weight:560;letter-spacing:-.01em;
  padding:.5rem 1.1rem;transition:transform .12s ease,background .15s ease,box-shadow .15s ease;
  box-shadow:0 1px 2px rgba(0,0,0,.05);}
.stButton>button:hover,.stDownloadButton>button:hover{background:#fafafa;border-color:rgba(0,0,0,.14);}
.stButton>button:active,.stDownloadButton>button:active{transform:scale(.97);}
.stDownloadButton>button{background:var(--accent);color:#fff;border-color:transparent;}
.stDownloadButton>button:hover{background:var(--accent-press);}

/* expanders as cards ---------------------------------------------------- */
[data-testid="stExpander"]{border:1px solid var(--border);border-radius:var(--radius);
  background:var(--surface);backdrop-filter:blur(20px) saturate(180%);
  -webkit-backdrop-filter:blur(20px) saturate(180%);box-shadow:var(--shadow);
  overflow:hidden;margin-bottom:.7rem;}
[data-testid="stExpander"] summary{font-weight:590;letter-spacing:-.01em;color:var(--ink);padding:.2rem .1rem;}
[data-testid="stExpander"] summary:hover{color:var(--accent);}

/* inputs + uploader ----------------------------------------------------- */
[data-baseweb="input"],[data-baseweb="base-input"]{border-radius:10px!important;}
.stTextInput input{border-radius:10px;}
[data-testid="stFileUploaderDropzone"]{border-radius:12px;border:1px dashed rgba(0,0,0,.16);
  background:rgba(0,0,0,.02);}

/* dataframe ------------------------------------------------------------- */
[data-testid="stDataFrame"]{border:1px solid var(--border);border-radius:12px;overflow:hidden;
  box-shadow:var(--shadow);}

/* alerts ---------------------------------------------------------------- */
[data-testid="stAlert"]{border-radius:12px;border:1px solid var(--hair);}

/* empty state ----------------------------------------------------------- */
.empty{background:var(--surface);border:1px solid var(--border);border-radius:20px;
  box-shadow:var(--shadow);padding:2.4rem 2rem;text-align:center;margin-top:.5rem;}
.empty__t{font-size:1.15rem;font-weight:600;letter-spacing:-.02em;color:var(--ink);}
.empty__s{margin-top:.4rem;color:var(--ink2);font-size:.95rem;}
hr{border-color:var(--hair);}

/* tool switcher: st.segmented_control styled like the tab bar (hooks: data-testid, ARIA) */
[data-testid="stButtonGroup"] [role="radiogroup"]{gap:4px;background:rgba(0,0,0,.05);padding:5px;
  border-radius:13px;border:none;}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"]{border:none;border-radius:9px;
  background:transparent;color:var(--ink2);font-weight:560;letter-spacing:-.01em;padding:.42rem 1rem;
  transition:color .15s ease,background .15s ease,box-shadow .15s ease;}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"] p{color:inherit;font-weight:560;}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"]:hover{color:var(--ink);background:transparent;}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"][aria-checked="true"]{
  background:var(--solid);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.14);}
"""


def inject_css() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s))


def hero(title: str) -> None:
    st.markdown(f'<div class="hero"><div class="hero__title">{esc(title)}</div></div>',
                unsafe_allow_html=True)


def tiles(items: list[tuple[str, str, str]]) -> None:
    """Stat tiles in one row, however many there are (two per row on narrow screens)."""
    cells = "".join(
        f'<div class="tile tile--{tone}"><div class="tile__v">{esc(value)}</div>'
        f'<div class="tile__l">{esc(label)}</div></div>'
        for label, value, tone in items
    )
    st.markdown(f'<div class="tiles" style="--n:{len(items)}">{cells}</div>', unsafe_allow_html=True)
