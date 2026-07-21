import streamlit as st
import hashlib
import bcrypt
from utils import get_db_connection

st.set_page_config(page_title="Mushroom Farm OS", layout="wide")

if 'last_processed_file' not in st.session_state:
    st.session_state.last_processed_file = None
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = ""

st.markdown("""
    <style>
    .main { background-color: transparent; }
    [data-testid="stMetricValue"] {
        font-size: 28px;
        color: #4CAF50 !important;
    }
    [data-testid="stMetric"] {
        background-color: rgba(128, 128, 128, 0.1);
        padding: 10px;
        border-radius: 10px;
        border: 1px solid rgba(128, 128, 128, 0.2);
    }
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
    conn = get_db_connection()
    cursor = conn.execute("SELECT password FROM users WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    if result is None:
        return False
    stored = result[0]
    # Bcrypt hashes start with $2b$ — legacy SHA-256 hashes do not
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        return bcrypt.checkpw(password.encode(), stored.encode())
    # Legacy SHA-256 password — verify then upgrade to bcrypt in-place
    if stored == hashlib.sha256(password.encode()).hexdigest():
        new_hash = _hash_bcrypt(password)
        conn2 = get_db_connection()
        conn2.execute("UPDATE users SET password = ? WHERE username = ?", (new_hash, username))
        conn2.commit()
        conn2.close()
        return True
    return False

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
    st.write("<br><br><br>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("""
            <div style='text-align: center; padding-bottom: 20px;'>
                <h1 style='color: #4CAF50; font-size: 3.5rem; margin-bottom: 0px;'>🍄 Mushroom OS</h1>
                <p style='color: #AAAAAA; font-size: 1.1rem; margin-top: 5px;'>Please log in to access your secure farm dashboard.</p>
            </div>
            """, unsafe_allow_html=True)

        tab1, tab2 = st.tabs(["🔒 Log In", "📝 Sign Up"])
        with tab1:
            locked, secs_left = _is_locked_out()
            if locked:
                st.error(f"🔒 Too many failed attempts. Please wait **{secs_left} seconds** before trying again.")
                st.rerun()
            with st.form("login_form", border=True):
                l_user = st.text_input("Username")
                l_pass = st.text_input("Password", type="password")
                st.write("")
                if st.form_submit_button("Log In", use_container_width=True):
                    locked, secs_left = _is_locked_out()
                    if locked:
                        st.error(f"🔒 Too many failed attempts. Wait {secs_left}s.")
                    elif verify_user(l_user, l_pass):
                        st.session_state.logged_in       = True
                        st.session_state.username        = l_user
                        st.session_state.login_attempts  = 0
                        st.session_state.lockout_until   = None
                        st.success("Login successful!")
                        st.rerun()
                    else:
                        import time
                        st.session_state.login_attempts += 1
                        remaining = MAX_ATTEMPTS - st.session_state.login_attempts
                        if st.session_state.login_attempts >= MAX_ATTEMPTS:
                            st.session_state.lockout_until = time.time() + LOCKOUT_SECS
                            st.error(f"🔒 Too many failed attempts. Locked out for {LOCKOUT_SECS} seconds.")
                        else:
                            st.error(f"Incorrect username or password. {remaining} attempt(s) left.")

        with tab2:
            with st.form("signup_form", border=True):
                s_user = st.text_input("New Username")
                s_pass = st.text_input("New Password", type="password")
                s_conf = st.text_input("Confirm Password", type="password")
                st.caption("Password must be at least 8 characters with uppercase, lowercase, number, and special character (!@#$%^&* etc.)")
                st.write("")
                if st.form_submit_button("Create Account", use_container_width=True):
                    import re
                    errors = []
                    if len(s_user) < 3:
                        errors.append("Username must be at least 3 characters.")
                    if len(s_pass) < 8:
                        errors.append("Password must be at least 8 characters.")
                    if not re.search(r'[A-Z]', s_pass):
                        errors.append("Password must contain at least one uppercase letter (A-Z).")
                    if not re.search(r'[a-z]', s_pass):
                        errors.append("Password must contain at least one lowercase letter (a-z).")
                    if not re.search(r'\d', s_pass):
                        errors.append("Password must contain at least one number (0-9).")
                    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', s_pass):
                        errors.append("Password must contain at least one special character (!@#$%^&* etc.).")
                    if s_pass != s_conf:
                        errors.append("Passwords do not match.")
                    if errors:
                        for e in errors:
                            st.error(e)
                    else:
                        if create_user(s_user, s_pass):
                            st.success("Account created! Please switch to the Log In tab.")
                        else:
                            st.error("Username already exists!")
    st.stop()

# --- NAVIGATION ---
st.sidebar.markdown(f"**Welcome, {st.session_state.username}!**")
page = st.sidebar.radio("Go to:", [
    "Dashboard",
    "Forecasting",
    "Record Situation",
    "Record Planting",
    "Quality Analysis",
    "AI Image Detection",
    "SOP Procedures",
    "Generate Report",
])
st.sidebar.markdown("---")
if st.sidebar.button("Log Out"):
    st.session_state.logged_in = False
    st.session_state.username = ""
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
