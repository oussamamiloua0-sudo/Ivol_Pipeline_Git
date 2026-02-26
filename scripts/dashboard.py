"""iVol Options Dashboard — Streamlit app.

Run locally:
    streamlit run scripts/dashboard.py

On the droplet it is managed by systemd (ivol-dashboard.service).
Access at http://147.182.205.5:8501
"""
from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db.engine import get_engine           # noqa: E402
from src.export.query import fetch_export_df   # noqa: E402

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="iVol Options Dashboard",
    page_icon="📈",
    layout="wide",
)

st.title("📈 iVol Options Dashboard")

# ---------------------------------------------------------------------------
# Engine (cached for the process lifetime)
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
        rows = conn.execute(text("SELECT symbol FROM dim_underlying ORDER BY symbol")).fetchall()
    return [r[0] for r in rows]


@st.cache_data(ttl=CACHE_TTL)
def get_date_range(symbol: str) -> tuple[str, str]:
    with _engine().connect() as conn:
        row = conn.execute(text("""
            SELECT MIN(f.trade_date), MAX(f.trade_date)
            FROM fact_option_eod f
            JOIN dim_option_contract c ON c.option_id = f.option_id
            JOIN dim_underlying u      ON u.underlying_id = c.underlying_id
            WHERE u.symbol = :sym
        """), {"sym": symbol}).fetchone()
    if row and row[0]:
        return str(row[0]), str(row[1])
    return "", ""


@st.cache_data(ttl=CACHE_TTL)
def get_expirations(symbol: str, start: str, end: str) -> list[str]:
    with _engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT c.expiration_date
            FROM fact_option_eod f
            JOIN dim_option_contract c ON c.option_id = f.option_id
            JOIN dim_underlying u      ON u.underlying_id = c.underlying_id
            WHERE u.symbol = :sym
              AND f.trade_date BETWEEN :start AND :end
            ORDER BY c.expiration_date
        """), {"sym": symbol, "start": start, "end": end}).fetchall()
    return [str(r[0]) for r in rows]


@st.cache_data(ttl=CACHE_TTL)
def get_trade_dates(symbol: str, start: str, end: str) -> list[str]:
    with _engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT f.trade_date
            FROM fact_option_eod f
            JOIN dim_option_contract c ON c.option_id = f.option_id
            JOIN dim_underlying u      ON u.underlying_id = c.underlying_id
            WHERE u.symbol = :sym
              AND f.trade_date BETWEEN :start AND :end
            ORDER BY f.trade_date
        """), {"sym": symbol, "start": start, "end": end}).fetchall()
    return [str(r[0]) for r in rows]


@st.cache_data(ttl=CACHE_TTL)
def load_data(symbol: str, start: str, end: str) -> pd.DataFrame:
    return fetch_export_df(_engine(), symbols=[symbol], start=start, end=end)


# ---------------------------------------------------------------------------
# Excel export helper
# ---------------------------------------------------------------------------
def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="options")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Sidebar — filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 10 min")

    symbols = get_symbols()
    if not symbols:
        st.error("No symbols found in DB.")
        st.stop()

    symbol = st.selectbox("Symbol", symbols, index=symbols.index("SPY") if "SPY" in symbols else 0)

    db_min, db_max = get_date_range(symbol)
    if not db_min:
        st.warning(f"No data for {symbol}.")
        st.stop()

    import datetime
    db_min_date = datetime.date.fromisoformat(db_min)
    db_max_date = datetime.date.fromisoformat(db_max)

    date_range = st.date_input(
        "Date range",
        value=(db_min_date, db_max_date),
        min_value=db_min_date,
        max_value=db_max_date,
        format="YYYY-MM-DD",
    )

    # Handle partial selection (user picked only one date)
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
        start_date = end_date = date_range[0]
        st.info("Pick an end date to complete the range.")
        st.stop()
    else:
        start_date = end_date = date_range

    if start_date > end_date:
        st.error("Start date must be before end date.")
        st.stop()

    start_str = str(start_date)
    end_str   = str(end_date)

    cp_filter = st.radio("Call / Put", ["All", "C", "P"], horizontal=True)

    load_clicked = st.button("▶ Load data", type="primary", use_container_width=True)

    st.divider()
    st.caption(f"DB range: {db_min} → {db_max}")

# ---------------------------------------------------------------------------
# Load data — only on explicit button click, stored in session_state
# ---------------------------------------------------------------------------
ROW_CAP = 25_000

if load_clicked:
    with st.spinner("Loading data…"):
        raw = load_data(symbol, start_str, end_str)
    st.session_state["df"]      = raw
    st.session_state["df_meta"] = (symbol, start_str, end_str)

if "df" not in st.session_state:
    st.info("Select your filters in the sidebar then click **▶ Load data**.")
    st.stop()

df = st.session_state["df"]
loaded_symbol, loaded_start, loaded_end = st.session_state["df_meta"]

truncated = len(df) > ROW_CAP
if truncated:
    df = df.head(ROW_CAP)
    st.warning(f"Result capped at {ROW_CAP:,} rows. Narrow the date range for full data.")

if df.empty:
    st.warning("No data found for the selected filters.")
    st.stop()

if cp_filter != "All":
    df = df[df["call_put"] == cp_filter]

# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Rows",        f"{len(df):,}")
c2.metric("Trading days", df["trade_date"].nunique())
c3.metric("Expirations",  df["expiration_date"].nunique())
c4.metric("Strikes",      df["strike"].nunique())

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_data, tab_smile, tab_ivtime = st.tabs(["📋 Data Table", "😊 IV Smile", "📅 IV Over Time"])

# --- Tab 1: Data table ---
with tab_data:
    st.caption(f"Showing {len(df):,} rows — {symbol} {start_str} → {end_str}")

    # Compact display: format floats
    display_df = df.copy()
    for col in ["bid", "ask", "price", "iv", "preiv", "delta", "gamma", "vega", "theta", "rho"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].round(4)

    st.dataframe(display_df, use_container_width=True, height=500)

    col_dl1, col_dl2, _ = st.columns([1, 1, 4])
    with col_dl1:
        st.download_button(
            "⬇️ Download CSV",
            data=df.to_csv(index=False).encode(),
            file_name=f"{loaded_symbol}_{loaded_start}_{loaded_end}.csv",
            mime="text/csv",
        )
    with col_dl2:
        if st.button("⬇️ Generate Excel", key="gen_excel"):
            st.session_state["excel_bytes"] = to_excel_bytes(df)
        if "excel_bytes" in st.session_state:
            st.download_button(
                "⬇️ Download Excel",
                data=st.session_state["excel_bytes"],
                file_name=f"{loaded_symbol}_{loaded_start}_{loaded_end}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_excel",
            )

# --- Tab 2: IV Smile ---
with tab_smile:
    trade_dates = sorted(df["trade_date"].astype(str).unique())
    expirations = sorted(df["expiration_date"].astype(str).unique())

    if not trade_dates or not expirations:
        st.info("No data to plot.")
    else:
        sc1, sc2 = st.columns(2)
        with sc1:
            sel_date = st.selectbox("Trade date", trade_dates, index=len(trade_dates)-1, key="smile_date")
        with sc2:
            sel_exp  = st.selectbox("Expiration", expirations, key="smile_exp")

        smile_df = df[(df["trade_date"].astype(str) == sel_date) &
                      (df["expiration_date"].astype(str) == sel_exp)]

        if smile_df.empty:
            st.info("No data for this date / expiration combination.")
        else:
            calls = smile_df[smile_df["call_put"] == "C"][["strike", "iv"]].rename(columns={"iv": "Call IV"})
            puts  = smile_df[smile_df["call_put"] == "P"][["strike", "iv"]].rename(columns={"iv": "Put IV"})
            merged = pd.merge(calls, puts, on="strike", how="outer").sort_values("strike")

            st.line_chart(merged.set_index("strike"), use_container_width=True)
            st.caption(f"IV Smile — {symbol}  trade={sel_date}  exp={sel_exp}")

# --- Tab 3: IV Over Time ---
with tab_ivtime:
    exps_for_time = sorted(df["expiration_date"].astype(str).unique())

    if not exps_for_time:
        st.info("No data to plot.")
    else:
        tc1, tc2 = st.columns(2)
        with tc1:
            sel_exp_t = st.selectbox("Expiration", exps_for_time, key="time_exp")
        with tc2:
            sel_cp_t  = st.radio("Call / Put", ["C", "P"], horizontal=True, key="time_cp")

        time_df = (
            df[(df["expiration_date"].astype(str) == sel_exp_t) & (df["call_put"] == sel_cp_t)]
            .copy()
        )

        if time_df.empty:
            st.info("No data for this expiration / type.")
        else:
            # ATM = strike closest to median strike
            mid_strike = time_df.groupby("trade_date")["strike"].median()
            atm_rows = []
            for td, ms in mid_strike.items():
                day_df = time_df[time_df["trade_date"] == td]
                closest = day_df.iloc[(day_df["strike"] - ms).abs().argsort()[:1]]
                atm_rows.append(closest)

            atm_df = pd.concat(atm_rows).sort_values("trade_date")[["trade_date", "strike", "iv", "delta"]]
            atm_df["trade_date"] = pd.to_datetime(atm_df["trade_date"])
            atm_df = atm_df.set_index("trade_date")

            st.line_chart(atm_df[["iv"]], use_container_width=True)
            st.caption(f"ATM IV over time — {symbol}  exp={sel_exp_t}  {sel_cp_t}")
            st.dataframe(atm_df.reset_index(), use_container_width=True, height=300)
