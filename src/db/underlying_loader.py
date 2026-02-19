from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def get_or_create_underlying_id(engine: Engine, symbol: str) -> int:
    sql = text("""
        INSERT INTO dim_underlying(symbol)
        VALUES (:symbol)
        ON CONFLICT (symbol) DO UPDATE SET symbol = EXCLUDED.symbol
        RETURNING underlying_id;
    """)
    with engine.begin() as conn:
        underlying_id = conn.execute(sql, {"symbol": symbol}).scalar_one()
    return int(underlying_id)


def upsert_underlying_eod(engine: Engine, underlying_id: int, row: dict) -> None:
    sql = text("""
        INSERT INTO fact_underlying_eod
            (trade_date, underlying_id, open, high, low, close, adj_close, volume)
        VALUES
            (:trade_date, :underlying_id, :open, :high, :low, :close, :adj_close, :volume)
        ON CONFLICT (trade_date, underlying_id) DO UPDATE SET
            open      = EXCLUDED.open,
            high      = EXCLUDED.high,
            low       = EXCLUDED.low,
            close     = EXCLUDED.close,
            adj_close = EXCLUDED.adj_close,
            volume    = EXCLUDED.volume;
    """)

    payload = {
        "trade_date": row.get("date"),
        "underlying_id": underlying_id,
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "adj_close": row.get("adj_close", row.get("unadjusted_close")),
        "volume": row.get("volume"),
    }

    with engine.begin() as conn:
        conn.execute(sql, payload)
