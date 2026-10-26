import streamlit as st

st.set_page_config(
    page_title="Crypto App",
    page_icon="₿",
    layout="wide",
)

# ── Shared sidebar navigation styles (applies on every page) ─────────────────
st.markdown("""
<style>
/* Sidebar page navigation — consistent size + hover on every page */
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"],
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] span,
section[data-testid="stSidebar"] nav a,
section[data-testid="stSidebar"] nav span,
section[data-testid="stSidebar"] ul li a {
    font-size: 17px !important;
    font-weight: 600 !important;
    color: #EDEDED !important;
    font-family: 'Courier New', monospace !important;
    letter-spacing: .02em !important;
    transition: color .15s ease, background-color .15s ease !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
    padding: 6px 10px !important;
    border-radius: 4px !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a:hover,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a:hover span,
section[data-testid="stSidebar"] nav a:hover,
section[data-testid="stSidebar"] nav a:hover span,
section[data-testid="stSidebar"] ul li a:hover {
    color: #39FF14 !important;
    background-color: rgba(57,255,20,0.08) !important;
}
/* Currently active page */
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"],
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] span,
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a[aria-current="page"],
section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a[aria-current="page"] span {
    color: #00FFFF !important;
    font-weight: 700 !important;
}
</style>
""", unsafe_allow_html=True)

pages = [
    st.Page("0_home.py", title="HOME", icon="🏠", url_path="home"),
    st.Page("1_News_Feed.py", title="NEWS FEED", icon="📰", url_path="news-feed"),
    st.Page("2_Sectors.py", title="SECTORS", icon="📊", url_path="sectors"),
    st.Page("scanner_hub.py", title="SCANNER", icon="🔍", url_path="scanner"),
    st.Page("5_Celestial_Intel.py", title="CELESTIAL INTEL", icon="🌙", url_path="celestial-intel"),
    st.Page("6_Shill_Tracker.py", title="SHILL TRACKER", icon="📡", url_path="shill-tracker"),
]

pg = st.navigation(pages)

pg.run()
