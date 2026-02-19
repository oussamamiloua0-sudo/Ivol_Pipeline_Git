import argparse
import json
import os
import sys
from pathlib import Path
from datetime import date as dt_date

import requests
from sqlalchemy import MetaData, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.db.engine import get_engine

BASE_URL_DEFAULT = "https://restapi.ivolatility.com"


def sanitize_url(url: str) -> str:
    return url.replace(os.getenv("IVOL_API_KEY", ""), "****") if os.getenv("IVOL_API_KEY") else url


def find_env_file(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        candidate = p / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env() -> Path | None:
    env_path = find_env_file(Path(__file__).resolve().parent)
    if not env_path:
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value
    return env_path


def detect_col(tbl, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in tbl.c:
            return c
    return None


def filter_to_table_cols(tbl, data: dict) -> dict:
    return {k: v for k, v in data.items() if k in tbl.c}


def upsert_one(conn, tbl, values: dict, conflict_cols: list[str]) -> None:
    values = filter_to_table_cols(tbl, values)
    if not values:
        return
    stmt = mysql_insert(tbl).values(**values)
    update_cols = {k: stmt.inserted[k] for k in values.keys() if k not in conflict_cols}
    if update_cols:
        stmt = stmt.on_duplicate_key_update(**update_cols)
    else:
        stmt = stmt.prefix_with("IGNORE")
    conn.execute(stmt)


def ensure_underlying(conn, tbl_underlying, symbol: str) -> int | None:
    id_col = detect_col(tbl_underlying, ["underlying_id", "id"])
    sym_col = detect_col(tbl_underlying, ["symbol", "ticker"])
    if not sym_col:
        raise RuntimeError("dim_underlying must have a symbol/ticker column.")

    if id_col:
        existing = conn.execute(
            select(tbl_underlying.c[id_col]).where(tbl_underlying.c[sym_col] == symbol).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return int(existing)

    upsert_one(conn, tbl_underlying, {sym_col: symbol}, conflict_cols=[sym_col])

    if not id_col:
        return None

    existing = conn.execute(
        select(tbl_underlying.c[id_col]).where(tbl_underlying.c[sym_col] == symbol).limit(1)
    ).scalar_one()
    return int(existing)


def _parse_date(s: str) -> dt_date:
    return dt_date.fromisoformat(s)


def _pick_first_row(payload: dict) -> dict | None:
    data = payload.get("data")
    if isinstance(data, list) and data:
        return data[0]
    return None


def _safe_bid_ask(close_val, bid_val, ask_val):
    if close_val is None:
        return None, None
    try:
        close_f = float(close_val)
    except Exception:
        return None, None
    if close_f <= 0:
        return None, None
    def _ok(v):
        try:
            f = float(v)
        except Exception:
            return None
        if f <= 0:
            return None
        ratio = f / close_f
        return f if 0.5 <= ratio <= 1.5 else None
    return _ok(bid_val), _ok(ask_val)


def run(symbol: str, trade_date: str, debug: bool = False) -> None:
    load_env()

    base_url = (os.getenv("IVOL_BASE_URL", BASE_URL_DEFAULT) or BASE_URL_DEFAULT).rstrip("/")
    api_key = (os.getenv("IVOL_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing IVOL_API_KEY in .env")

    db_url = (
        os.getenv("DATABASE_URL")
        or os.getenv("DB_URL")
        or os.getenv("SQLALCHEMY_DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or os.getenv("PG_DSN")
    )
    if not db_url:
        raise RuntimeError("Missing DB URL env var.")

    os.environ["DB_URL"] = db_url.strip().strip('"').strip("'")

    params = {"apiKey": api_key, "symbol": symbol, "date": trade_date}
    url = f"{base_url}/equities/eod/stock-prices"
    r = requests.get(url, params=params, timeout=60)
    print("UNDERLYING URL:", sanitize_url(r.url))
    print("UNDERLYING HTTP:", r.status_code)
    payload = r.json()
    row = _pick_first_row(payload)
    if not row:
        raise RuntimeError("No underlying EOD rows returned.")
    if debug:
        print("DEBUG top-level keys:", sorted(payload.keys()))
        print("DEBUG row keys:", sorted(row.keys()))

    eng = get_engine()
    md = MetaData()
    md.reflect(bind=eng, only=["dim_underlying", "fact_underlying_eod"])
    tbl_underlying = md.tables.get("dim_underlying")
    tbl_fact = md.tables.get("fact_underlying_eod")
    if tbl_underlying is None or tbl_fact is None:
        raise RuntimeError("Required tables not found: dim_underlying, fact_underlying_eod")

    trade_date_obj = _parse_date(trade_date)

    with eng.begin() as conn:
        underlying_id = ensure_underlying(conn, tbl_underlying, symbol)
        if underlying_id is None:
            raise RuntimeError("Could not resolve underlying_id.")

        raw_bid = row.get("bid") if "bid" in row else row.get("bidPrice")
        raw_ask = row.get("ask") if "ask" in row else row.get("askPrice")
        bid_val, ask_val = _safe_bid_ask(row.get("close"), raw_bid, raw_ask)

        values = {
            detect_col(tbl_fact, ["underlying_id"]): underlying_id,
            detect_col(tbl_fact, ["trade_date", "date"]): trade_date_obj,
            detect_col(tbl_fact, ["open"]): row.get("open"),
            detect_col(tbl_fact, ["high"]): row.get("high"),
            detect_col(tbl_fact, ["low"]): row.get("low"),
            detect_col(tbl_fact, ["close"]): row.get("close"),
            detect_col(tbl_fact, ["adj_close", "adjClose", "adjusted_close"]): row.get("adjClose")
            or row.get("adj_close")
            or row.get("adjusted_close"),
            detect_col(tbl_fact, ["volume"]): row.get("volume"),
            detect_col(tbl_fact, ["bid"]): bid_val if bid_val is not None else None,
            detect_col(tbl_fact, ["ask"]): ask_val if ask_val is not None else None,
        }
        values = {k: v for k, v in values.items() if k is not None}
        if debug:
            print(
                "DEBUG values:",
                {
                    "open": values.get(detect_col(tbl_fact, ["open"])),
                    "high": values.get(detect_col(tbl_fact, ["high"])),
                    "low": values.get(detect_col(tbl_fact, ["low"])),
                    "close": values.get(detect_col(tbl_fact, ["close"])),
                    "volume": values.get(detect_col(tbl_fact, ["volume"])),
                    "bid": values.get(detect_col(tbl_fact, ["bid"])),
                    "ask": values.get(detect_col(tbl_fact, ["ask"])),
                },
            )

        conflict_cols = [
            detect_col(tbl_fact, ["underlying_id"]),
            detect_col(tbl_fact, ["trade_date", "date"]),
        ]
        conflict_cols = [c for c in conflict_cols if c is not None]
        upsert_one(conn, tbl_fact, values, conflict_cols=conflict_cols)

    print(
        "UNDERLYING EOD OK:",
        f"symbol={symbol}",
        f"date={trade_date}",
        f"close={row.get('close')}",
        f"volume={row.get('volume')}",
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--debug", action="store_true", help="print keys and mapped values")
    args = p.parse_args()
    run(args.symbol.strip().upper(), args.date, debug=args.debug)


if __name__ == "__main__":
    main()
