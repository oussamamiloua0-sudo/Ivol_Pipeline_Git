"""iVol Options Dashboard — Streamlit app.

Run locally:
    streamlit run scripts/dashboard.py

On the droplet it is managed by systemd (ivol-dashboard.service).
Access at http://147.182.205.5:8501
"""
from __future__ import annotations

import datetime
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db.engine import get_engine          # noqa: E402
from src.export.query import fetch_export_df  # noqa: E402

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="iVol Options Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* ---- Header ---- */
.ivol-header {
    padding: 1.2rem 1.8rem;
    background: linear-gradient(135deg, #161b27 0%, #1e2d45 100%);
    border-radius: 12px;
    border: 1px solid #2d3f5e;
    margin-bottom: 1.5rem;
    display: flex;
    align-items: center;
    gap: 1rem;
}
.ivol-header h1 {
    margin: 0;
    font-size: 1.6rem;
    font-weight: 700;
    color: #e2e8f0;
    letter-spacing: -0.5px;
}
.ivol-header p {
    margin: 0.15rem 0 0 0;
    font-size: 0.82rem;
    color: #718096;
}
.ivol-badge {
    background: #4f8ef7;
    color: #fff;
    font-size: 0.7rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* ---- Metric cards ---- */
[data-testid="stMetric"] {
    background: #161b27;
    border: 1px solid #2d3f5e;
    border-radius: 10px;
    padding: 0.8rem 1rem !important;
}
[data-testid="stMetricLabel"] { color: #718096 !important; font-size: 0.75rem !important; }
[data-testid="stMetricValue"] { color: #e2e8f0 !important; font-size: 1.5rem !important; font-weight: 700 !important; }

/* ---- Tabs ---- */
[data-testid="stTabs"] button {
    font-weight: 600;
    font-size: 0.85rem;
}

/* ---- Sidebar ---- */
[data-testid="stSidebar"] {
    border-right: 1px solid #2d3f5e;
}
.sidebar-section {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #4f8ef7;
    margin: 1rem 0 0.4rem 0;
}

/* ---- Landing card ---- */
.landing-card {
    background: #161b27;
    border: 1px solid #2d3f5e;
    border-radius: 12px;
    padding: 2rem;
    text-align: center;
    margin-top: 2rem;
}
.landing-card h3 { color: #e2e8f0; margin-bottom: 0.5rem; }
.landing-card p  { color: #718096; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown("""
<div class="ivol-header">
    <div>
        <h1>📈 iVol Options Dashboard</h1>
        <p>Implied volatility data &nbsp;·&nbsp; Options chain explorer &nbsp;·&nbsp;
           <span class="ivol-badge">Live</span>
        </p>
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
@st.cache_resource
def _engine():
    return get_engine()


CACHE_TTL = 600  # 10 minutes

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL)
def get_symbols() -> list[str]:
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT symbol FROM dim_underlying ORDER BY symbol"
        )).fetchall()
    return [r[0] for r in rows]


@st.cache_data(ttl=CACHE_TTL)
def get_date_range(symbol: str) -> tuple[str, str]:
    with _engine().connect() as conn:
        row = conn.execute(text("""
            SELECT MIN(f.trade_date), MAX(f.trade_date)
            FROM fact_option_eod f
            JOIN dim_option_contract c ON c.option_id     = f.option_id
            JOIN dim_underlying u      ON u.underlying_id = c.underlying_id
            WHERE u.symbol = :sym
        """), {"sym": symbol}).fetchone()
    return (str(row[0]), str(row[1])) if row and row[0] else ("", "")


@st.cache_data(ttl=CACHE_TTL)
def load_data(symbol: str, start: str, end: str) -> pd.DataFrame:
    return fetch_export_df(_engine(), symbols=[symbol], start=start, end=end)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="options")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Filters")

    if st.button("🔄 Refresh cache", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Cache TTL: 10 min")

    st.divider()

    symbols = get_symbols()
    if not symbols:
        st.error("No symbols found in DB.")
        st.stop()

    symbol = st.selectbox(
        "Symbol",
        symbols,
        index=symbols.index("SPY") if "SPY" in symbols else 0,
    )

    db_min, db_max = get_date_range(symbol)
    if not db_min:
        st.warning(f"No data for {symbol}.")
        st.stop()

    db_min_date = datetime.date.fromisoformat(db_min)
    db_max_date = datetime.date.fromisoformat(db_max)

    date_range = st.date_input(
        "Date range",
        value=(db_min_date, db_max_date),
        min_value=db_min_date,
        max_value=db_max_date,
        format="YYYY-MM-DD",
    )

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
        st.info("Pick an end date.")
        st.stop()
    else:
        start_date = end_date = date_range

    if start_date > end_date:
        st.error("Start must be before end date.")
        st.stop()

    start_str = str(start_date)
    end_str   = str(end_date)

    cp_filter = st.radio("Call / Put", ["All", "C", "P"], horizontal=True)

    st.divider()
    load_clicked = st.button("▶ Load Data", type="primary", use_container_width=True)
    st.caption(f"Available: {db_min} → {db_max}")

# ---------------------------------------------------------------------------
# Load data on demand
# ---------------------------------------------------------------------------
ROW_CAP = 25_000

if load_clicked:
    with st.spinner(f"Loading {symbol} {start_str} → {end_str}…"):
        raw = load_data(symbol, start_str, end_str)
    st.session_state["df"]      = raw
    st.session_state["df_meta"] = (symbol, start_str, end_str)
    if "excel_bytes" in st.session_state:
        del st.session_state["excel_bytes"]

if "df" not in st.session_state:
    st.markdown("""
    <div class="landing-card">
        <h3>Select filters and click ▶ Load Data</h3>
        <p>Choose a symbol and date range in the sidebar, then load to explore<br>
        the options chain, IV smile, and implied volatility over time.</p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

df = st.session_state["df"]
loaded_symbol, loaded_start, loaded_end = st.session_state["df_meta"]

if len(df) > ROW_CAP:
    df = df.head(ROW_CAP)
    st.warning(f"Showing first {ROW_CAP:,} rows. Narrow the date range to see all data.")

if df.empty:
    st.warning("No data found for the selected filters.")
    st.stop()

if cp_filter != "All":
    df = df[df["call_put"] == cp_filter]

# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------
days  = df["trade_date"].nunique()
exps  = df["expiration_date"].nunique()
stks  = df["strike"].nunique()
avg_iv = df["iv"].mean()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Rows",          f"{len(df):,}")
c2.metric("Trading Days",  f"{days:,}")
c3.metric("Expirations",   f"{exps:,}")
c4.metric("Strikes",       f"{stks:,}")
c5.metric("Avg IV",        f"{avg_iv:.1%}" if pd.notna(avg_iv) else "—")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_data, tab_smile, tab_ivtime = st.tabs([
    "📋  Data Table",
    "📉  IV Smile",
    "📅  IV Over Time",
])

# ── Tab 1: Data Table ──────────────────────────────────────────────────────
with tab_data:
    st.caption(
        f"**{loaded_symbol}** · {loaded_start} → {loaded_end} · "
        f"{len(df):,} rows · {cp_filter if cp_filter != 'All' else 'Calls + Puts'}"
    )

    display_df = df.copy()
    for col in ["bid", "ask", "price", "iv", "preiv", "delta", "gamma", "vega", "theta", "rho"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].round(4)

    st.dataframe(
        display_df,
        use_container_width=True,
        height=480,
        column_config={
            "trade_date":      st.column_config.DateColumn("Trade Date", format="YYYY-MM-DD"),
            "expiration_date": st.column_config.DateColumn("Expiry",     format="YYYY-MM-DD"),
            "call_put":        st.column_config.TextColumn("C/P",  width="small"),
            "strike":          st.column_config.NumberColumn("Strike",  format="%.2f"),
            "dte":             st.column_config.NumberColumn("DTE",     format="%d"),
            "iv":              st.column_config.NumberColumn("IV",      format="%.4f"),
            "delta":           st.column_config.NumberColumn("Delta",   format="%.4f"),
            "gamma":           st.column_config.NumberColumn("Gamma",   format="%.4f"),
            "vega":            st.column_config.NumberColumn("Vega",    format="%.4f"),
            "theta":           st.column_config.NumberColumn("Theta",   format="%.4f"),
            "volume":          st.column_config.NumberColumn("Volume",  format="%d"),
            "open_interest":   st.column_config.NumberColumn("OI",      format="%d"),
        },
    )

    col_dl1, col_dl2, col_dl3 = st.columns([1, 1, 4])
    with col_dl1:
        st.download_button(
            "⬇️ CSV",
            data=df.to_csv(index=False).encode(),
            file_name=f"{loaded_symbol}_{loaded_start}_{loaded_end}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_dl2:
        if st.button("⚙️ Build Excel", use_container_width=True, key="gen_excel"):
            with st.spinner("Building Excel…"):
                st.session_state["excel_bytes"] = to_excel_bytes(df)
        if "excel_bytes" in st.session_state:
            st.download_button(
                "⬇️ Excel",
                data=st.session_state["excel_bytes"],
                file_name=f"{loaded_symbol}_{loaded_start}_{loaded_end}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_excel",
                use_container_width=True,
            )

# ── Tab 2: IV Smile ────────────────────────────────────────────────────────
with tab_smile:
    trade_dates = sorted(df["trade_date"].astype(str).unique())
    expirations = sorted(df["expiration_date"].astype(str).unique())

    if not trade_dates or not expirations:
        st.info("No data to plot. Load data first.")
    else:
        sc1, sc2 = st.columns(2)
        with sc1:
            sel_date = st.selectbox("Trade date", trade_dates,
                                    index=len(trade_dates) - 1, key="smile_date")
        with sc2:
            sel_exp = st.selectbox("Expiration", expirations, key="smile_exp")

        smile_df = df[
            (df["trade_date"].astype(str) == sel_date) &
            (df["expiration_date"].astype(str) == sel_exp)
        ]

        if smile_df.empty:
            st.info("No data for this date / expiration combination.")
        else:
            calls = smile_df[smile_df["call_put"] == "C"].sort_values("strike")
            puts  = smile_df[smile_df["call_put"] == "P"].sort_values("strike")

            fig = go.Figure()
            if not calls.empty:
                fig.add_trace(go.Scatter(
                    x=calls["strike"], y=calls["iv"],
                    mode="lines+markers", name="Call IV",
                    line=dict(color="#4f8ef7", width=2),
                    marker=dict(size=5),
                    hovertemplate="Strike: %{x}<br>IV: %{y:.4f}<extra>Call</extra>",
                ))
            if not puts.empty:
                fig.add_trace(go.Scatter(
                    x=puts["strike"], y=puts["iv"],
                    mode="lines+markers", name="Put IV",
                    line=dict(color="#f76f4f", width=2),
                    marker=dict(size=5),
                    hovertemplate="Strike: %{x}<br>IV: %{y:.4f}<extra>Put</extra>",
                ))
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0d1117",
                plot_bgcolor="#161b27",
                title=dict(
                    text=f"IV Smile — {loaded_symbol}  |  Trade: {sel_date}  |  Exp: {sel_exp}",
                    font=dict(size=14, color="#e2e8f0"),
                ),
                xaxis=dict(title="Strike", gridcolor="#2d3f5e"),
                yaxis=dict(title="Implied Volatility", gridcolor="#2d3f5e", tickformat=".2%"),
                legend=dict(bgcolor="rgba(0,0,0,0)"),
                hovermode="x unified",
                margin=dict(l=50, r=20, t=50, b=40),
                height=420,
            )
            st.plotly_chart(fig, use_container_width=True)

# ── Tab 3: IV Over Time ────────────────────────────────────────────────────
with tab_ivtime:
    exps_for_time = sorted(df["expiration_date"].astype(str).unique())

    if not exps_for_time:
        st.info("No data to plot. Load data first.")
    else:
        tc1, tc2 = st.columns(2)
        with tc1:
            sel_exp_t = st.selectbox("Expiration", exps_for_time, key="time_exp")
        with tc2:
            sel_cp_t = st.radio("Type", ["C", "P"], horizontal=True,
                                key="time_cp", captions=["Call", "Put"])

        time_df = df[
            (df["expiration_date"].astype(str) == sel_exp_t) &
            (df["call_put"] == sel_cp_t)
        ].copy()

        if time_df.empty:
            st.info("No data for this expiration / type.")
        else:
            # ATM: closest strike to daily median
            mid_strike = time_df.groupby("trade_date")["strike"].median()
            atm_rows = []
            for td, ms in mid_strike.items():
                day = time_df[time_df["trade_date"] == td]
                atm_rows.append(day.iloc[(day["strike"] - ms).abs().argsort()[:1]])
            atm_df = pd.concat(atm_rows).sort_values("trade_date")
            atm_df["trade_date"] = pd.to_datetime(atm_df["trade_date"])

            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=atm_df["trade_date"], y=atm_df["iv"],
                mode="lines+markers",
                name="ATM IV",
                line=dict(color="#4f8ef7", width=2),
                marker=dict(size=4),
                fill="tozeroy",
                fillcolor="rgba(79,142,247,0.08)",
                hovertemplate="%{x|%Y-%m-%d}<br>IV: %{y:.4f}<extra>ATM IV</extra>",
            ))
            fig2.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0d1117",
                plot_bgcolor="#161b27",
                title=dict(
                    text=f"ATM IV Over Time — {loaded_symbol}  |  Exp: {sel_exp_t}  |  {'Call' if sel_cp_t=='C' else 'Put'}",
                    font=dict(size=14, color="#e2e8f0"),
                ),
                xaxis=dict(title="Trade Date", gridcolor="#2d3f5e"),
                yaxis=dict(title="Implied Volatility", gridcolor="#2d3f5e", tickformat=".2%"),
                legend=dict(bgcolor="rgba(0,0,0,0)"),
                hovermode="x unified",
                margin=dict(l=50, r=20, t=50, b=40),
                height=420,
            )
            st.plotly_chart(fig2, use_container_width=True)

            with st.expander("ATM strikes table"):
                st.dataframe(
                    atm_df[["trade_date", "strike", "iv", "delta"]].reset_index(drop=True),
                    use_container_width=True,
                    height=280,
                    column_config={
                        "trade_date": st.column_config.DateColumn("Date",   format="YYYY-MM-DD"),
                        "strike":     st.column_config.NumberColumn("Strike", format="%.2f"),
                        "iv":         st.column_config.NumberColumn("IV",     format="%.4f"),
                        "delta":      st.column_config.NumberColumn("Delta",  format="%.4f"),
                    },
                )
