"""Ingest underlying EOD prices into fact_underlying_eod via yfinance.

Examples:
  # Backfill SPY for full year 2024
  python scripts/ingest_prices.py --symbol SPY --start 2024-01-01 --end 2024-12-31

  # Ingest yesterday for QQQ (default behaviour)
  python scripts/ingest_prices.py --symbol QQQ

  # Multiple symbols, date range
  python scripts/ingest_prices.py --symbol SPY QQQ AAPL --start 2023-01-01 --end 2023-12-31

  # Dry run — show what would be upserted without writing
  python scripts/ingest_prices.py --symbol SPY --start 2024-01-01 --end 2024-01-05 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date as dt_date, datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import yfinance as yf
from sqlalchemy import text

from src.db.engine import _load_env, get_engine     # noqa: E402
from src.ingest.writer import bulk_upsert            # noqa: E402
from src.ingest.schema_cache import SchemaCache, build_schema_cache  # noqa: E402


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(debug: bool, log_file: Optional[str]) -> logging.Logger:
    level  = logging.DEBUG if debug else logging.INFO
    fmt    = "%(asctime)s  %(levelname)-8s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    return logging.getLogger("ingest_prices")


# ---------------------------------------------------------------------------
# yfinance fetch
# ---------------------------------------------------------------------------

def _fetch_prices(
    ticker: str,
    start: dt_date,
    end: dt_date,
    logger: logging.Logger,
) -> pd.DataFrame:
    """Return DataFrame with columns [trade_date, open, high, low, close, volume].

    yfinance end date is exclusive, so we add one day.
    """
    yf_ticker = yf.Ticker(ticker)
    raw = yf_ticker.history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=True,
        actions=False,
    )
    if raw.empty:
        logger.warning("[%s] yfinance returned no data for %s → %s", ticker, start, end)
        return pd.DataFrame()

    raw = raw.reset_index()
    # Normalise column names (yfinance may return 'Date' or 'Datetime')
    raw.columns = [c.lower() for c in raw.columns]
    date_col = "date" if "date" in raw.columns else "datetime"
    raw["trade_date"] = pd.to_datetime(raw[date_col]).dt.date

    keep = ["trade_date", "open", "high", "low", "close", "volume"]
    for col in keep:
        if col not in raw.columns:
            raw[col] = None

    df = raw[keep].copy()
    df["volume"] = df["volume"].astype("Int64")   # nullable int
    logger.info("[%s] Fetched %d rows (%s -> %s)", ticker, len(df), start, end)
    return df


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_or_create_underlying(conn, symbol: str) -> int:
    """Return underlying_id for symbol, inserting into dim_underlying if absent."""
    row = conn.execute(
        text("SELECT underlying_id FROM dim_underlying WHERE symbol = :sym LIMIT 1"),
        {"sym": symbol},
    ).scalar_one_or_none()
    if row is not None:
        return int(row)

    conn.execute(
        text("INSERT IGNORE INTO dim_underlying (symbol) VALUES (:sym)"),
        {"sym": symbol},
    )
    return int(
        conn.execute(
            text("SELECT underlying_id FROM dim_underlying WHERE symbol = :sym LIMIT 1"),
            {"sym": symbol},
        ).scalar_one()
    )


def _upsert_prices(
    conn,
    underlying_id: int,
    df: pd.DataFrame,
    logger: logging.Logger,
    dry_run: bool,
) -> int:
    """Bulk-upsert rows from df into fact_underlying_eod. Returns row count."""
    if df.empty:
        return 0

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "underlying_id": underlying_id,
            "trade_date":    r["trade_date"],
            "open":          float(r["open"])   if r["open"]   is not None else None,
            "high":          float(r["high"])   if r["high"]   is not None else None,
            "low":           float(r["low"])    if r["low"]    is not None else None,
            "close":         float(r["close"])  if r["close"]  is not None else None,
            "volume":        int(r["volume"])   if pd.notna(r["volume"]) else None,
        })

    if dry_run:
        logger.info("[DRY RUN] Would upsert %d rows (underlying_id=%d)", len(rows), underlying_id)
        return len(rows)

    # Use raw MySQL INSERT ... ON DUPLICATE KEY UPDATE
    from sqlalchemy.dialects.mysql import insert as mysql_insert
    from sqlalchemy import Table, MetaData

    meta = MetaData()
    meta.reflect(bind=conn, only=["fact_underlying_eod"])
    tbl = meta.tables["fact_underlying_eod"]

    return bulk_upsert(conn, tbl, rows, conflict_cols=["underlying_id", "trade_date"])


# ---------------------------------------------------------------------------
# Per-symbol ingestion
# ---------------------------------------------------------------------------

def _ingest_symbol(
    symbol: str,
    start: dt_date,
    end: dt_date,
    engine,
    logger: logging.Logger,
    dry_run: bool,
) -> int:
    """Fetch + upsert prices for one symbol. Returns number of rows written."""
    df = _fetch_prices(symbol, start, end, logger)
    if df.empty:
        return 0

    with engine.begin() as conn:
        underlying_id = _get_or_create_underlying(conn, symbol)
        logger.info("[%s] underlying_id = %d", symbol, underlying_id)
        n = _upsert_prices(conn, underlying_id, df, logger, dry_run)

    action = "Would write" if dry_run else "Wrote"
    logger.info("[%s] %s %d rows into fact_underlying_eod", symbol, action, n)
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    yesterday = (dt_date.today() - timedelta(days=1)).isoformat()

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--symbol",    nargs="+", default=["SPY"],
                    help="One or more ticker symbols (default: SPY)")
    ap.add_argument("--start",     default=None, metavar="YYYY-MM-DD",
                    help="Start date (default: yesterday)")
    ap.add_argument("--end",       default=None, metavar="YYYY-MM-DD",
                    help="End date inclusive (default: yesterday)")
    ap.add_argument("--dry-run",   action="store_true", dest="dry_run",
                    help="Fetch data but do not write to DB")
    ap.add_argument("--log-file",  default=None, dest="log_file",
                    help="Optional path to write log output")
    ap.add_argument("--debug",     action="store_true")
    args = ap.parse_args()

    logger = _setup_logging(args.debug, args.log_file or f"logs/ingest_prices_{ts}.log")

    _load_env()

    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL") or ""
    if not db_url:
        raise SystemExit("ERROR: DB_URL not set in .env")
    os.environ["DB_URL"] = db_url.strip().strip('"').strip("'")

    start_str = args.start or yesterday
    end_str   = args.end   or yesterday
    start = dt_date.fromisoformat(start_str)
    end   = dt_date.fromisoformat(end_str)
    if start > end:
        raise SystemExit(f"ERROR: --start {start} is after --end {end}")

    engine = get_engine()

    total = 0
    for raw_sym in args.symbol:
        symbol = raw_sym.upper()
        logger.info("=== %s  %s -> %s%s ===", symbol, start, end,
                    "  [DRY RUN]" if args.dry_run else "")
        try:
            n = _ingest_symbol(symbol, start, end, engine, logger, args.dry_run)
            total += n
        except Exception as exc:
            logger.error("[%s] Failed: %s", symbol, exc, exc_info=args.debug)

    logger.info("Done. Total rows: %d", total)


if __name__ == "__main__":
    main()
