import streamlit as st
import hashlib
import bcrypt
from utils import get_db_connection
from translations import t

st.set_page_config(page_title="Projek Cendawan Berintegrasi AI", layout="wide")

if 'last_processed_file' not in st.session_state:
    st.session_state.last_processed_file = None
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = ""
if 'role' not in st.session_state:
    st.session_state.role = "user"
if 'lang' not in st.session_state:
    st.session_state.lang = "en"

st.markdown("""
    <style>
    /* ── GLOBAL BACKGROUND ──────────────────────────────────────── */
    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(ellipse at 8% 8%,  rgba(76,175,80,0.08) 0%, transparent 48%),
            radial-gradient(ellipse at 92% 92%, rgba(56,142,60,0.06) 0%, transparent 48%),
            linear-gradient(160deg, #060c06 0%, #0a130a 100%) !important;
    }
    [data-testid="stHeader"] {
        background: rgba(6,12,6,0.75) !important;
        backdrop-filter: blur(12px) !important;
        border-bottom: 1px solid rgba(76,175,80,0.1) !important;
    }

    /* ── SIDEBAR ────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        background: rgba(7,13,7,0.98) !important;
        border-right: 1px solid rgba(76,175,80,0.12) !important;
    }

    /* ── METRICS ────────────────────────────────────────────────── */
    [data-testid="stMetricValue"] { font-size: 28px; color: #4CAF50 !important; }
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, rgba(76,175,80,0.1), rgba(56,142,60,0.04)) !important;
        padding: 16px !important;
        border-radius: 14px !important;
        border: 1px solid rgba(76,175,80,0.22) !important;
        box-shadow: 0 4px 18px rgba(0,0,0,0.4) !important;
    }

    /* ── FORM CARD ──────────────────────────────────────────────── */
    [data-testid="stForm"] {
        background: rgba(255,255,255,0.025) !important;
        border: 1px solid rgba(76,175,80,0.22) !important;
        border-radius: 18px !important;
        box-shadow: 0 10px 48px rgba(0,0,0,0.55),
                    inset 0 1px 0 rgba(255,255,255,0.05) !important;
    }

    /* ── TEXT INPUTS ────────────────────────────────────────────── */
    [data-testid="stTextInput"] > div > div > input {
        background: rgba(255,255,255,0.05) !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: 9px !important;
        color: #e8f5e9 !important;
        transition: border 0.2s, box-shadow 0.2s, background 0.2s !important;
    }
    [data-testid="stTextInput"] > div > div > input:focus {
        border-color: rgba(76,175,80,0.6) !important;
        background: rgba(76,175,80,0.05) !important;
        box-shadow: 0 0 0 3px rgba(76,175,80,0.13) !important;
    }

    /* ── SUBMIT BUTTONS ─────────────────────────────────────────── */
    [data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(135deg, #2e7d32, #43a047, #388e3c) !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        letter-spacing: 0.05em !important;
        color: #fff !important;
        box-shadow: 0 4px 22px rgba(76,175,80,0.38),
                    inset 0 1px 0 rgba(255,255,255,0.14) !important;
        transition: box-shadow 0.25s, transform 0.25s !important;
    }
    [data-testid="stFormSubmitButton"] > button:hover {
        box-shadow: 0 7px 30px rgba(76,175,80,0.58) !important;
        transform: translateY(-2px) !important;
    }

    /* ── TABS ───────────────────────────────────────────────────── */
    [data-baseweb="tab-list"] {
        background: rgba(255,255,255,0.04) !important;
        border-radius: 10px !important;
        padding: 4px !important;
    }
    [data-baseweb="tab"][aria-selected="true"] {
        background: rgba(76,175,80,0.16) !important;
        border-radius: 7px !important;
    }

    /* ── SELECTBOX ──────────────────────────────────────────────── */
    [data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child {
        background: rgba(255,255,255,0.05) !important;
        border: 1px solid rgba(76,175,80,0.28) !important;
        border-radius: 8px !important;
    }
    [data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child:hover {
        border-color: rgba(76,175,80,0.55) !important;
    }

    /* ── LOGIN GLOW ANIMATION ───────────────────────────────────── */
    @keyframes loginGlow {
        0%, 100% { filter: drop-shadow(0 0 18px rgba(76,175,80,0.28)); }
        50%       { filter: drop-shadow(0 0 38px rgba(76,175,80,0.55)); }
    }
    .login-title-glow { animation: loginGlow 4s ease-in-out infinite; }
    </style>
    """, unsafe_allow_html=True)

# --- DB SETUP (runs once per app process) ---
@st.cache_resource
def _init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS situation_reports
                 (date TEXT, status TEXT, disease_noted TEXT, quality TEXT, notes TEXT, username TEXT)''')
    for _col in [
        "ALTER TABLE situation_reports ADD COLUMN block_id TEXT DEFAULT '-'",
        "ALTER TABLE situation_reports ADD COLUMN section_id TEXT DEFAULT '-'",
        "ALTER TABLE situation_reports ADD COLUMN photo TEXT",
    ]:
        try:
            conn.execute(_col)
            conn.commit()
        except Exception:
            pass
    conn.execute('''CREATE TABLE IF NOT EXISTS planting_records
                 (block_id TEXT, species TEXT, planted_date TEXT, notes TEXT, predicted_harvest TEXT, username TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ai_harvest_logs
                 (timestamp TEXT, filename TEXT, young INTEGER, ready INTEGER, old INTEGER, total_clusters INTEGER, username TEXT)''')
    for _col in [
        "ALTER TABLE ai_harvest_logs ADD COLUMN original_url TEXT",
        "ALTER TABLE ai_harvest_logs ADD COLUMN analyzed_url TEXT",
        "ALTER TABLE ai_harvest_logs ADD COLUMN section_id TEXT DEFAULT '-'",
        "ALTER TABLE ai_harvest_logs RENAME COLUMN old TO overripe",
    ]:
        try:
            conn.execute(_col)
            conn.commit()
        except Exception:
            pass
    conn.execute('''CREATE TABLE IF NOT EXISTS harvest_history
                 (block_id TEXT, harvest_number INTEGER, harvest_date TEXT, username TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT)''')
    for _col in [
        "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'",
    ]:
        try:
            conn.execute(_col)
            conn.commit()
        except Exception:
            pass
    # Pre-create admin account if it doesn't exist
    _admin_user = "kkc@admin"
    _admin_pass = "Admin@123!"
    _existing = conn.execute("SELECT username FROM users WHERE username = ?", (_admin_user,)).fetchone()
    if _existing is None:
        conn.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (_admin_user, bcrypt.hashpw(_admin_pass.encode(), bcrypt.gensalt()).decode(), "majikan")
        )
        conn.commit()
    conn.close()
    return True

_init_db()

# --- AUTH ---
def _hash_bcrypt(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def create_user(username, password):
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, _hash_bcrypt(password)))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def verify_user(username, password):
    """Returns (success: bool, role: str). Role defaults to 'user'."""
    conn = get_db_connection()
    cursor = conn.execute("SELECT password, role FROM users WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    if result is None:
        return False, "user"
    stored, role = result[0], (result[1] or "user")
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        ok = bcrypt.checkpw(password.encode(), stored.encode())
        return ok, (role if ok else "user")
    if stored == hashlib.sha256(password.encode()).hexdigest():
        new_hash = _hash_bcrypt(password)
        conn2 = get_db_connection()
        conn2.execute("UPDATE users SET password = ? WHERE username = ?", (new_hash, username))
        conn2.commit()
        conn2.close()
        return True, role
    return False, "user"

# --- BRUTE FORCE PROTECTION ---
MAX_ATTEMPTS  = 3
LOCKOUT_SECS  = 30

if 'login_attempts' not in st.session_state:
    st.session_state.login_attempts = 0
if 'lockout_until' not in st.session_state:
    st.session_state.lockout_until = None

def _is_locked_out():
    if st.session_state.lockout_until is None:
        return False, 0
    import time
    remaining = st.session_state.lockout_until - time.time()
    if remaining > 0:
        return True, int(remaining)
    st.session_state.lockout_until  = None
    st.session_state.login_attempts = 0
    return False, 0

if not st.session_state.logged_in:
    # Language selector — top left
    _LANG_MAP = {"🇬🇧 English": "en", "🇲🇾 Melayu": "ms", "🇨🇳 中文": "zh"}
    _lang_labels = list(_LANG_MAP.keys())
    _current_label = [k for k, v in _LANG_MAP.items() if v == st.session_state.lang][0]
    _lc1, _ = st.columns([1, 4])
    with _lc1:
        _sel_lang = st.selectbox(
            "🌐 " + t('nav_lang'), _lang_labels,
            index=_lang_labels.index(_current_label),
            key="lang_login_select", label_visibility="collapsed"
        )
    if _LANG_MAP[_sel_lang] != st.session_state.lang:
        st.session_state.lang = _LANG_MAP[_sel_lang]
        st.rerun()

    import base64 as _b64
    with open("picture/kkc_logo.png", "rb") as _lf:
        _kkc_b64 = _b64.b64encode(_lf.read()).decode()

    # Decorative background blobs
    st.markdown("""
        <div style='position:fixed;top:0;left:0;width:100vw;height:100vh;
                    pointer-events:none;z-index:0;overflow:hidden;'>
            <div style='position:absolute;top:-180px;right:-180px;width:600px;height:600px;
                        background:radial-gradient(circle,rgba(76,175,80,0.07) 0%,transparent 70%);
                        border-radius:50%;'></div>
            <div style='position:absolute;bottom:-220px;left:-180px;width:700px;height:700px;
                        background:radial-gradient(circle,rgba(56,142,60,0.05) 0%,transparent 70%);
                        border-radius:50%;'></div>
            <div style='position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
                        font-size:30rem;opacity:0.013;user-select:none;line-height:1;
                        pointer-events:none;'>🍄</div>
        </div>
    """, unsafe_allow_html=True)

    st.write("<br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown(f"""
            <div style='text-align:center; padding-bottom:20px;'>
                <div class='login-title-glow'
                     style='display:inline-flex; align-items:center; gap:18px;
                            justify-content:center; margin-bottom:14px; flex-wrap:wrap;'>
                    <img src='data:image/png;base64,{_kkc_b64}'
                         style='height:78px; width:auto; object-fit:contain;
                                max-width:220px; max-height:78px;
                                border-radius:18px;
                                box-shadow:0 6px 24px rgba(0,0,0,0.55),
                                           0 0 0 2px rgba(255,255,255,0.08);'>
                    <div style='text-align:center; width:100%; display:flex;
                                flex-direction:column; align-items:center;'>
                        <h1 style='color:#5dba60; font-size:2.0rem; font-weight:800; margin:0;
                                   letter-spacing:-0.01em; line-height:1.25;
                                   text-shadow:0 0 36px rgba(76,175,80,0.45);'>
                            Projek Cendawan Berintegrasi AI
                        </h1>
                        <div style='color:#556655; font-size:0.72rem; font-weight:600;
                                    letter-spacing:0.12em; text-transform:uppercase;
                                    margin-top:5px; text-align:center; white-space:nowrap;
                                    width:auto;'>
                            PERAK CULINARY ARTS ACADEMY @KOLEJ KOMUNITI CHENDEROH
                        </div>
                    </div>
                </div>
                <p style='color:#707070; font-size:0.92rem; margin:0; letter-spacing:0.025em;'>
                    {t('login_subtitle')}
                </p>
                <div style='margin-top:10px; color:#445544; font-size:0.7rem;
                            letter-spacing:0.1em; text-transform:uppercase; font-weight:500;'>
                    In collaboration with &nbsp;
                    <span style='color:#5a7a5a; font-weight:700;'>
                        Universiti Sains Malaysia (USM)
                    </span>
                </div>
            </div>
        """, unsafe_allow_html=True)

        tab1, tab2 = st.tabs([t('login_tab'), t('signup_tab')])
        with tab1:
            locked, secs_left = _is_locked_out()
            if locked:
                st.error(f"🔒 {t('login_locked', s=secs_left)}")
                st.rerun()
            with st.form("login_form", border=True):
                l_user = st.text_input(t('login_username'))
                l_pass = st.text_input(t('login_password'), type="password")
                st.write("")
                if st.form_submit_button(t('login_btn'), use_container_width=True):
                    locked, secs_left = _is_locked_out()
                    if locked:
                        st.error(f"🔒 {t('login_locked_short', s=secs_left)}")
                    else:
                        _ok, _role = verify_user(l_user, l_pass)
                        if _ok:
                            st.session_state.logged_in      = True
                            st.session_state.username       = l_user
                            st.session_state.role           = _role
                            st.session_state.login_attempts = 0
                            st.session_state.lockout_until  = None
                            st.success(t('login_success'))
                            st.rerun()
                        else:
                            import time
                            st.session_state.login_attempts += 1
                            remaining = MAX_ATTEMPTS - st.session_state.login_attempts
                            if st.session_state.login_attempts >= MAX_ATTEMPTS:
                                st.session_state.lockout_until = time.time() + LOCKOUT_SECS
                                st.error(f"🔒 {t('login_lockout', s=LOCKOUT_SECS)}")
                            else:
                                st.error(t('login_wrong', r=remaining))

        with tab2:
            with st.form("signup_form", border=True):
                s_user = st.text_input(t('signup_new_user'))
                s_pass = st.text_input(t('signup_new_pass'), type="password")
                s_conf = st.text_input(t('signup_confirm'), type="password")
                st.caption(t('signup_hint'))
                st.write("")
                if st.form_submit_button(t('signup_btn'), use_container_width=True):
                    import re
                    errors = []
                    if len(s_user) < 3:
                        errors.append(t('signup_err_len3'))
                    if len(s_pass) < 8:
                        errors.append(t('signup_err_len8'))
                    if not re.search(r'[A-Z]', s_pass):
                        errors.append(t('signup_err_upper'))
                    if not re.search(r'[a-z]', s_pass):
                        errors.append(t('signup_err_lower'))
                    if not re.search(r'\d', s_pass):
                        errors.append(t('signup_err_digit'))
                    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', s_pass):
                        errors.append(t('signup_err_special'))
                    if s_pass != s_conf:
                        errors.append(t('signup_err_match'))
                    if errors:
                        for e in errors:
                            st.error(e)
                    else:
                        if create_user(s_user, s_pass):
                            st.success(t('signup_success'))
                        else:
                            st.error(t('signup_exists'))
    st.stop()

# ---- NAVIGATION ----
_is_admin   = st.session_state.get('role') == 'majikan'
_role_badge = " 👑" if _is_admin else ""
st.sidebar.markdown(f"**{t('nav_welcome')}, {st.session_state.username}{_role_badge}!**")

st.sidebar.markdown("---")

if _is_admin:
    _nav_pages = ["Admin Panel", "Forecasting", "Quality Analysis", "Generate Report"]
else:
    _nav_pages = ["Dashboard", "Record Situation", "Record Planting", "AI Image Detection", "SOP Procedures"]

page = st.sidebar.radio(
    t('nav_go_to'), _nav_pages,
    format_func=lambda p: t(f'nav_{p}')
)
st.sidebar.markdown("---")
if st.sidebar.button(t('nav_logout')):
    st.session_state.logged_in = False
    st.session_state.username  = ""
    st.session_state.role      = "user"
    st.rerun()

# ---- PAGE ROUTING ----
if page == "Dashboard":
    from views.dashboard import show
    show()
elif page == "Forecasting":
    from views.monitor import show
    show()
elif page == "Record Situation":
    from views.situation import show
    show()
elif page == "Record Planting":
    from views.planting import show
    show()
elif page == "SOP Procedures":
    from views.sop import show
    show()
elif page == "Quality Analysis":
    from views.quality import show
    show()
elif page == "AI Image Detection":
    from views.detection import show
    show()
elif page == "Generate Report":
    from views.report import show
    show()
elif page == "Admin Panel":
    if _is_admin:
        from views.admin import show
        show()
    else:
        st.error("Access denied.")
