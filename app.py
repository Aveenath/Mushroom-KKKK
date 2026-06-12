import streamlit as st
import hashlib
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

# --- DB SETUP ---
conn = get_db_connection()
conn.execute('''CREATE TABLE IF NOT EXISTS situation_reports
             (date TEXT, status TEXT, disease_noted TEXT, quality TEXT, notes TEXT, username TEXT)''')
for _col in [
    "ALTER TABLE situation_reports ADD COLUMN block_id TEXT DEFAULT '-'",
    "ALTER TABLE situation_reports ADD COLUMN section_id TEXT DEFAULT '-'",
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
conn.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT)''')
conn.close()

# --- AUTH ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, password):
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hash_password(password)))
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
    return result is not None and result[0] == hash_password(password)

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
            with st.form("login_form", border=True):
                l_user = st.text_input("Username")
                l_pass = st.text_input("Password", type="password")
                st.write("")
                if st.form_submit_button("Log In", use_container_width=True):
                    if verify_user(l_user, l_pass):
                        st.session_state.logged_in = True
                        st.session_state.username = l_user
                        st.success("Login successful!")
                        st.rerun()
                    else:
                        st.error("Incorrect username or password")

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
    "Live Monitor & Forecast",
    "Record Situation",
    "Record Planting",
    "Quality Analysis",
    "AI Image Detection",
    "SOP Procedures",
])
st.sidebar.markdown("---")
if st.sidebar.button("Log Out"):
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()

# --- PAGE ROUTING ---
if page == "Live Monitor & Forecast":
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
