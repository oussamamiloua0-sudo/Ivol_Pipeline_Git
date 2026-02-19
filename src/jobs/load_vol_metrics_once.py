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


def _pick_ivx(row: dict) -> float | None:
    for k in (
        "30d IV Mean",
        "30d IV Call",
        "30d IV Put",
        "30d IV",
        "ivx",
        "ivx30",
        "ivx_30",
        "ivx1m",
        "ivx_1m",
    ):
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except Exception:
                return None
    return None


def _pick_hv(row: dict, key: str) -> float | None:
    for k in (key, key.replace("_", ""), key.upper()):
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except Exception:
                return None
    return None


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

    ivx_url = f"{base_url}/equities/eod/ivx"
    ivx_resp = requests.get(ivx_url, params=params, timeout=60)
    print("IVX URL:", sanitize_url(ivx_resp.url))
    print("IVX HTTP:", ivx_resp.status_code)
    ivx_payload = ivx_resp.json()
    ivx_row = _pick_first_row(ivx_payload) or {}

    hv_url = f"{base_url}/equities/eod/hv"
    hv_resp = requests.get(hv_url, params=params, timeout=60)
    print("HV URL:", sanitize_url(hv_resp.url))
    print("HV HTTP:", hv_resp.status_code)
    hv_payload = hv_resp.json()
    hv_row = _pick_first_row(hv_payload) or {}
    if debug:
        print("DEBUG IVX top-level keys:", sorted(ivx_payload.keys()))
        print("DEBUG IVX row keys:", sorted(ivx_row.keys()))
        print("DEBUG HV top-level keys:", sorted(hv_payload.keys()))
        print("DEBUG HV row keys:", sorted(hv_row.keys()))

    eng = get_engine()
    md = MetaData()
    md.reflect(bind=eng, only=["dim_underlying", "fact_vol_metrics"])
    tbl_underlying = md.tables.get("dim_underlying")
    tbl_fact = md.tables.get("fact_vol_metrics")
    if tbl_underlying is None or tbl_fact is None:
        raise RuntimeError("Required tables not found: dim_underlying, fact_vol_metrics")

    trade_date_obj = _parse_date(trade_date)
    ivx_val = _pick_ivx(ivx_row)

    hv_values = {
        "hv_10": _pick_hv(hv_row, "10d HV") or _pick_hv(hv_row, "hv_10") or _pick_hv(hv_row, "hv10"),
        "hv_20": _pick_hv(hv_row, "20d HV") or _pick_hv(hv_row, "hv_20") or _pick_hv(hv_row, "hv20"),
        "hv_30": _pick_hv(hv_row, "30d HV") or _pick_hv(hv_row, "hv_30") or _pick_hv(hv_row, "hv30"),
        "hv_60": _pick_hv(hv_row, "60d HV") or _pick_hv(hv_row, "hv_60") or _pick_hv(hv_row, "hv60"),
        "hv_90": _pick_hv(hv_row, "90d HV") or _pick_hv(hv_row, "hv_90") or _pick_hv(hv_row, "hv90"),
        "hv_252": _pick_hv(hv_row, "252d HV") or _pick_hv(hv_row, "hv_252") or _pick_hv(hv_row, "hv252"),
    }
    if hv_values.get("hv_30") is None:
        single_hv = _pick_hv(hv_row, "HV") or _pick_hv(hv_row, "hv")
        if single_hv is not None:
            hv_values["hv_30"] = single_hv
    if debug:
        print(
            "DEBUG values:",
            {
                "ivx": ivx_val,
                "hv_10": hv_values.get("hv_10"),
                "hv_20": hv_values.get("hv_20"),
                "hv_30": hv_values.get("hv_30"),
                "hv_60": hv_values.get("hv_60"),
                "hv_90": hv_values.get("hv_90"),
                "hv_252": hv_values.get("hv_252"),
            },
        )

    with eng.begin() as conn:
        underlying_id = ensure_underlying(conn, tbl_underlying, symbol)
        if underlying_id is None:
            raise RuntimeError("Could not resolve underlying_id.")

        values = {
            detect_col(tbl_fact, ["underlying_id"]): underlying_id,
            detect_col(tbl_fact, ["trade_date", "date"]): trade_date_obj,
            detect_col(tbl_fact, ["ivx"]): ivx_val,
            detect_col(tbl_fact, ["hv_10", "hv10"]): hv_values.get("hv_10"),
            detect_col(tbl_fact, ["hv_20", "hv20"]): hv_values.get("hv_20"),
            detect_col(tbl_fact, ["hv_30", "hv30"]): hv_values.get("hv_30"),
            detect_col(tbl_fact, ["hv_60", "hv60"]): hv_values.get("hv_60"),
            detect_col(tbl_fact, ["hv_90", "hv90"]): hv_values.get("hv_90"),
            detect_col(tbl_fact, ["hv_252", "hv252"]): hv_values.get("hv_252"),
            detect_col(tbl_fact, ["ivx_raw"]): json.dumps(ivx_payload),
            detect_col(tbl_fact, ["hv_raw"]): json.dumps(hv_payload),
        }
        values = {k: v for k, v in values.items() if k is not None}

        conflict_cols = [
            detect_col(tbl_fact, ["underlying_id"]),
            detect_col(tbl_fact, ["trade_date", "date"]),
        ]
        conflict_cols = [c for c in conflict_cols if c is not None]
        upsert_one(conn, tbl_fact, values, conflict_cols=conflict_cols)

    print(
        "VOL METRICS OK:",
        f"symbol={symbol}",
        f"date={trade_date}",
        f"ivx={ivx_val}",
        f"hv_30={hv_values.get('hv_30')}",
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
