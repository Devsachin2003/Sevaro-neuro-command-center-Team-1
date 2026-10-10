"""Sevaro-style look for the Streamlit app (font, colours, cards, pill buttons, header).

Brand tokens taken from sevaro.com: Nunito Sans, navy ink #111B29, slate body #677489,
eyebrow grey #97A3B7, surface #F4F5F7, accent gradient teal #24C3B8 -> indigo #5D5DE5.
Status colours (critical red / warning amber) are kept separate and always paired
with an icon + label, so brand colour never carries alert meaning.
"""
import streamlit as st

INK, BODY, EYEBROW, SURFACE, LINE = "#111B29", "#677489", "#97A3B7", "#F4F5F7", "#E3E7EE"
TEAL, INDIGO, PURPLE = "#24C3B8", "#5D5DE5", "#350894"
GRADIENT = f"linear-gradient(90deg, {TEAL} 0%, {INDIGO} 100%)"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;500;600;700;800;900&display=swap');
html, body, p, li, label, h1, h2, h3, h4, button, input, textarea, .stMarkdown {{
  font-family: 'Nunito Sans', -apple-system, 'Segoe UI', sans-serif;
}}
[data-testid="stIconMaterial"], .material-symbols-rounded {{ font-family: 'Material Symbols Rounded' !important; }}
.stApp {{ background: {SURFACE}; }}
.block-container {{ padding-top: 3.2rem; max-width: 1500px; }}
h1, h2, h3, h4 {{ color: {INK}; font-weight: 800 !important; letter-spacing: -0.01em; }}
.stMarkdown p, .stMarkdown li {{ color: #2B3445; }}
[data-testid="stCaptionContainer"] {{ color: {BODY}; }}

/* ---- header ---- */
.sv-header {{ display:flex; align-items:center; justify-content:space-between; gap:16px;
  background:#fff; border:1px solid {LINE}; border-radius:18px; padding:14px 22px; margin-bottom:14px;
  box-shadow:0 1px 3px rgba(17,27,41,.05); position:relative; overflow:hidden; }}
.sv-header:after {{ content:""; position:absolute; left:0; right:0; bottom:0; height:3px; background:{GRADIENT}; }}
.sv-brand {{ display:flex; align-items:center; gap:14px; }}
.sv-mark {{ width:38px; height:38px; border-radius:12px; background:{GRADIENT}; display:flex;
  align-items:center; justify-content:center; font-size:20px; box-shadow:0 6px 14px rgba(93,93,229,.25); }}
.sv-word {{ font-weight:700; letter-spacing:.32em; color:{INK}; font-size:13px; }}
.sv-title {{ font-weight:900; color:{INK}; font-size:22px; line-height:1.15; }}
.sv-sub {{ color:{BODY}; font-size:13px; font-weight:500; }}
.sv-chips {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
.sv-chip {{ border:1px solid {LINE}; border-radius:999px; padding:5px 12px; font-size:12px; font-weight:700;
  color:{INK}; background:#fff; white-space:nowrap; }}
.sv-chip.now {{ background:{GRADIENT}; color:#fff; border:none; }}
.sv-eyebrow {{ text-transform:uppercase; letter-spacing:.12em; font-size:11px; font-weight:700;
  color:{EYEBROW}; margin:6px 0 -6px 0; }}

/* ---- buttons: gradient pills like sevaro.com CTAs ---- */
.stButton > button, .stDownloadButton > button {{ border-radius:999px; font-weight:700; }}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
  background:{GRADIENT}; border:none; color:#fff; box-shadow:0 6px 16px rgba(93,93,229,.22); }}
.stButton > button[kind="primary"]:hover {{ filter:brightness(1.06); color:#fff; }}
.stButton > button[kind="secondary"] {{ border-color:{LINE}; color:{INK}; background:#fff; }}
.stButton > button[kind="tertiary"] {{ color:{INDIGO}; font-weight:700; padding-left:0; }}

/* ---- cards ---- */
[data-testid="stVerticalBlockBorderWrapper"] {{ background:#fff; border-radius:16px !important;
  border-color:{LINE} !important; box-shadow:0 1px 3px rgba(17,27,41,.05); }}
[data-testid="stMetric"] {{ background:#fff; border:1px solid {LINE}; border-radius:16px; padding:12px 16px; }}
[data-testid="stMetricLabel"] p {{ text-transform:uppercase; letter-spacing:.1em; font-size:11px !important;
  font-weight:700; color:{EYEBROW}; }}
[data-testid="stMetricValue"] {{ font-weight:900; color:{INK}; }}
[data-testid="stExpander"] details {{ background:#fff; border-radius:14px; border-color:{LINE}; }}
[data-testid="stExpander"] summary p {{ font-weight:700; color:{INK}; }}
.st-key-topcard {{ background:linear-gradient(#fff,#fff) padding-box, {GRADIENT} border-box;
  border:2px solid transparent; border-image:none; border-radius:18px; padding:4px 6px; }}

/* ---- tabs ---- */
.stTabs [data-baseweb="tab-list"] {{ gap:6px; background:#fff; border:1px solid {LINE}; border-radius:999px;
  padding:5px; width:fit-content; }}
.stTabs [data-baseweb="tab"] {{ border-radius:999px; padding:6px 16px; height:auto; font-weight:700; color:{BODY}; }}
.stTabs [aria-selected="true"] {{ background:{GRADIENT}; color:#fff !important; }}
.stTabs [aria-selected="true"] p {{ color:#fff !important; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display:none; }}

/* ---- sidebar ---- */
[data-testid="stSidebar"] {{ border-right:1px solid {LINE}; }}
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{ font-size:15px; }}

/* ---- message bubble (offer preview) ---- */
.sv-msg {{ background:#EEF0FD; border:1px solid #D9DCF8; border-radius:16px 16px 16px 4px; padding:12px 14px;
  color:{INK}; font-size:14px; line-height:1.5; max-width:640px; margin:4px 0 10px; }}

/* ---- phone mock ---- */
.st-key-phone {{ border:10px solid {INK} !important; border-radius:34px !important; padding:18px 14px 22px;
  box-shadow:0 18px 40px rgba(17,27,41,.18); max-width:380px; margin:0 auto; }}
.sv-phone-top {{ display:flex; justify-content:space-between; font-size:12px; color:{BODY}; font-weight:700; }}
.sv-phone-hello {{ font-size:22px; font-weight:900; color:{INK}; margin:6px 0 2px; }}
</style>
"""


def apply_theme():
    st.markdown(CSS, unsafe_allow_html=True)


def header(now_label: str, n_critical: int, n_short: int):
    st.markdown(f"""
<div class="sv-header">
  <div class="sv-brand">
    <div class="sv-mark">🧠</div>
    <div>
      <div class="sv-word">SEVARO</div>
      <div class="sv-title">Tele-Neurology Operations Command Center</div>
      <div class="sv-sub">Stroke &amp; General Neuro coverage across the hospital network · Detect → Decide → Act</div>
    </div>
  </div>
  <div class="sv-chips">
    <span class="sv-chip now">Now {now_label} ET</span>
    <span class="sv-chip">🔴 {n_critical} critical</span>
    <span class="sv-chip">▲ {n_short} hospitals short soon</span>
  </div>
</div>""", unsafe_allow_html=True)


def eyebrow(text: str):
    st.markdown(f'<div class="sv-eyebrow">{text}</div>', unsafe_allow_html=True)
