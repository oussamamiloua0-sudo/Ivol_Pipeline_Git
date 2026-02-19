import argparse
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


def find_env_file(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        candidate = p / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env() -> None:
    env_path = find_env_file(Path(__file__).resolve().parent)
    if not env_path:
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def detect_col(tbl, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in tbl.c:
            return c
    return None


def filter_to_table_cols(tbl, data: dict) -> dict:
    return {k: v for k, v in data.items() if k in tbl.c and v is not None}


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


def coerce_date(v):
    if v is None:
        return None
    if isinstance(v, dt_date):
        return v
    s = str(v).strip()
    if not s:
        return None
    return dt_date.fromisoformat(s[:10])


def parse_iso_date(s: str) -> dt_date:
    return dt_date.fromisoformat(s)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = p.parse_args()

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

    params = {"apiKey": api_key, "symbol": args.symbol, "date": args.date}
    url = f"{base_url}/equities/eod/options-rawiv"
    r = requests.get(url, params=params, timeout=120)
    print("FULL CHAIN HTTP:", r.status_code)
    payload = r.json()
    data = payload.get("data", []) or []
    print("FULL CHAIN ROWS:", len(data))

    eng = get_engine()
    md = MetaData()
    md.reflect(
        bind=eng,
        only=["dim_underlying", "dim_option_contract", "fact_option_eod"],
    )
    tbl_underlying = md.tables.get("dim_underlying")
    tbl_contract = md.tables.get("dim_option_contract")
    tbl_fact = md.tables.get("fact_option_eod")
    if tbl_underlying is None or tbl_contract is None or tbl_fact is None:
        raise RuntimeError("Required tables not found.")

    contract_oid_col = detect_col(tbl_contract, ["option_id", "optionId"])
    fact_oid_col = detect_col(tbl_fact, ["option_id", "optionId"])
    fact_date_col = detect_col(tbl_fact, ["trade_date", "date", "asof_date"])
    if not contract_oid_col or not fact_oid_col or not fact_date_col:
        raise RuntimeError("Missing option_id or trade_date columns.")

    run_date = parse_iso_date(args.date)

    ok = 0
    with eng.begin() as conn:
        underlying_id = ensure_underlying(conn, tbl_underlying, args.symbol)
        if underlying_id is None:
            raise RuntimeError("Could not resolve underlying_id.")

        for row in data:
            option_id = row.get("option_id") or row.get("optionId")
            if option_id is None:
                continue
            option_id = int(option_id)

            exp_col = detect_col(tbl_contract, ["expiration_date", "expiration", "expiry", "expirationDate"])
            exp_val = coerce_date(row.get("expiration") or row.get("expiration_date") or row.get("expirationDate"))

            contract_values = {
                contract_oid_col: option_id,
                detect_col(tbl_contract, ["underlying_id"]): underlying_id,
                detect_col(tbl_contract, ["symbol"]): row.get("symbol"),
                detect_col(tbl_contract, ["exchange"]): row.get("exchange"),
                detect_col(tbl_contract, ["option_symbol", "option symbol"]): row.get("option symbol")
                or row.get("option_symbol")
                or row.get("optionSymbol"),
                exp_col: exp_val,
                detect_col(tbl_contract, ["strike"]): row.get("strike"),
                detect_col(tbl_contract, ["call_put", "callput", "Call/Put"]): row.get("Call/Put")
                or row.get("call_put")
                or row.get("callPut"),
                detect_col(tbl_contract, ["style"]): row.get("style"),
            }
            contract_values = {k: v for k, v in contract_values.items() if k is not None}
            upsert_one(conn, tbl_contract, contract_values, conflict_cols=[contract_oid_col])

            fact_values = {
                fact_oid_col: option_id,
                fact_date_col: run_date,
                detect_col(tbl_fact, ["bid"]): row.get("bid"),
                detect_col(tbl_fact, ["ask"]): row.get("ask"),
                detect_col(tbl_fact, ["price", "mid"]): row.get("price"),
                detect_col(tbl_fact, ["iv"]): row.get("iv"),
                detect_col(tbl_fact, ["preiv"]): row.get("preiv"),
                detect_col(tbl_fact, ["delta"]): row.get("delta"),
                detect_col(tbl_fact, ["gamma"]): row.get("gamma"),
                detect_col(tbl_fact, ["vega"]): row.get("vega"),
                detect_col(tbl_fact, ["theta"]): row.get("theta"),
                detect_col(tbl_fact, ["rho"]): row.get("rho"),
                detect_col(tbl_fact, ["volume"]): row.get("volume"),
                detect_col(tbl_fact, ["open_interest", "open interest"]): row.get("open interest")
                or row.get("open_interest"),
                detect_col(tbl_fact, ["is_settlement"]): row.get("is_settlement"),
            }
            fact_values = {k: v for k, v in fact_values.items() if k is not None}
            upsert_one(conn, tbl_fact, fact_values, conflict_cols=[fact_oid_col, fact_date_col])
            ok += 1

    print("UPSERTED:", ok)


if __name__ == "__main__":
    main()
