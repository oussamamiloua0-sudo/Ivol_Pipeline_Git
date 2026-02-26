"""Export query — produces the canonical 21-column DataFrame matching the Excel spec.

Target column order (matches AAPL_backfill_2022-01-03_2022-01-04.xlsx + style):
  trade_date, underlying, option_id, option_symbol, call_put, strike,
  expiration_date, dte, style, bid, ask, price, iv, preiv,
  delta, gamma, vega, theta, rho, volume, open_interest
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

# Columns in the exact order the Excel spec requires.
EXPORT_COLUMNS = [
    "trade_date",
    "underlying",
    "option_id",
    "option_symbol",
    "call_put",
    "strike",
    "expiration_date",
    "dte",
    "style",
    "bid",
    "ask",
    "price",
    "iv",
    "preiv",
    "delta",
    "gamma",
    "vega",
    "theta",
    "rho",
    "volume",
    "open_interest",
]

_SQL = """
SELECT
    f.trade_date,
    u.symbol                                     AS underlying,
    c.option_id,
    c.option_symbol,
    c.call_put,
    c.strike,
    c.expiration_date,
    DATEDIFF(c.expiration_date, f.trade_date)    AS dte,
    c.style,
    f.bid,
    f.ask,
    f.price,
    f.iv,
    f.preiv,
    f.delta,
    f.gamma,
    f.vega,
    f.theta,
    f.rho,
    f.volume,
    f.open_interest
FROM fact_option_eod f
JOIN dim_option_contract c ON c.option_id     = f.option_id
JOIN dim_underlying u      ON u.underlying_id = c.underlying_id
WHERE 1=1
  {symbol_clause}
  {date_clause}
ORDER BY f.trade_date, c.expiration_date, c.call_put, c.strike
"""


def fetch_export_df(
    engine: Engine,
    symbols: list[str] | None = None,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Return a DataFrame with exactly EXPORT_COLUMNS.

    Filter options (all optional):
      symbols  — list of underlying symbols, e.g. ['SPX', 'AAPL']
      date     — single date 'YYYY-MM-DD' (overrides start/end)
      start    — range start 'YYYY-MM-DD'
      end      — range end   'YYYY-MM-DD'
    """
    params: dict = {}

    if symbols:
        placeholders = ", ".join(f":sym{i}" for i in range(len(symbols)))
        symbol_clause = f"AND u.symbol IN ({placeholders})"
        for i, s in enumerate(symbols):
            params[f"sym{i}"] = s
    else:
        symbol_clause = ""

    if date:
        date_clause = "AND f.trade_date = :date"
        params["date"] = date
    elif start and end:
        date_clause = "AND f.trade_date BETWEEN :start AND :end"
        params["start"] = start
        params["end"]   = end
    elif start:
        date_clause = "AND f.trade_date >= :start"
        params["start"] = start
    elif end:
        date_clause = "AND f.trade_date <= :end"
        params["end"] = end
    else:
        date_clause = ""

    sql = _SQL.format(symbol_clause=symbol_clause, date_clause=date_clause)

    with engine.connect() as conn:
        df = pd.read_sql_query(text(sql), conn, params=params)

    # Enforce exact column order and drop any extras.
    for col in EXPORT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[EXPORT_COLUMNS]
