"""Quick equity price chart from fact_underlying_eod.

Usage:
  python scripts/chart_equity.py --symbol SPY
  python scripts/chart_equity.py --symbol SPY QQQ IWM
"""
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text
from src.db.engine import _load_env, get_engine

ap = argparse.ArgumentParser()
ap.add_argument("--symbol", nargs="+", default=["SPY"])
args = ap.parse_args()

_load_env()
engine = get_engine()

fig = go.Figure()

with engine.connect() as conn:
    for sym in args.symbol:
        df = pd.read_sql(text("""
            SELECT f.trade_date, f.close
            FROM fact_underlying_eod f
            JOIN dim_underlying u ON f.underlying_id = u.underlying_id
            WHERE u.symbol = :sym
            ORDER BY f.trade_date
        """), conn, params={"sym": sym})

        if df.empty:
            print(f"No data for {sym}")
            continue

        base = df["close"].iloc[0]
        df["pct"] = (df["close"] / base - 1) * 100

        fig.add_trace(go.Scatter(
            x=df["trade_date"], y=df["pct"],
            name=sym, mode="lines", line=dict(width=2)
        ))
        print(f"{sym}: {len(df)} rows | {df['trade_date'].min()} to {df['trade_date'].max()} | total return: {df['pct'].iloc[-1]:.1f}%")

fig.update_layout(
    title="Equity Total Return (% from first date)",
    xaxis_title="Date",
    yaxis_title="Return (%)",
    hovermode="x unified",
    template="plotly_white",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)

fig.show()
