import os
import sys
import argparse
import requests
from pathlib import Path
from datetime import date as dt_date
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

from dotenv import load_dotenv

from sqlalchemy import MetaData, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from db.engine import get_engine

BASE_URL_DEFAULT = "https://restapi.ivolatility.com"


# -------------------------
# Helpers
# -------------------------
def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    q2 = [(k, ("****" if k.lower() in ("apikey", "token", "password") else v)) for k, v in q]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q2), parts.fragment))


def find_env_file(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        candidate = p / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env():
    env_path = find_env_file(Path(__file__).resolve().parent)
    if env_path:
        load_dotenv(env_path, override=True)
    return env_path


def detect_col(tbl, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in tbl.c:
            return c
    return None


def filter_to_table_cols(tbl, data: dict) -> dict:
    return {k: v for k, v in data.items() if k in tbl.c and v is not None}


def parse_iso_date(s: str) -> dt_date:
    return dt_date.fromisoformat(s)


def coerce_date(v):
    if v is None:
        return None
    if isinstance(v, dt_date):
        return v
    s = str(v).strip()
    if not s:
        return None
    return dt_date.fromisoformat(s[:10])


def upsert_one(conn, tbl, values: dict, conflict_cols: list[str]) -> None:
    """
    Dialect-aware upsert:
      - Postgres: INSERT .. ON CONFLICT DO UPDATE/NOTHING
      - MySQL:    INSERT .. ON DUPLICATE KEY UPDATE (or INSERT IGNORE)
    """
    values = filter_to_table_cols(tbl, values)
    if not values:
        return

    dialect = conn.dialect.name  # "mysql" or "postgresql"

    if dialect == "postgresql":
        stmt = pg_insert(tbl).values(**values)
        update_cols = {k: getattr(stmt.excluded, k) for k in values.keys() if k not in conflict_cols}
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        conn.execute(stmt)
        return

    if dialect == "mysql":
        stmt = mysql_insert(tbl).values(**values)
        update_cols = {k: stmt.inserted[k] for k in values.keys() if k not in conflict_cols}
        if update_cols:
            stmt = stmt.on_duplicate_key_update(**update_cols)
        else:
            stmt = stmt.prefix_with("IGNORE")
        conn.execute(stmt)
        return

    raise RuntimeError(f"Unsupported SQL dialect for upsert: {dialect}")


# -------------------------
# iVol calls
# -------------------------
def get_nearest_option_ids(base_url: str, api_key: str, symbol: str, date: str, dte: int,
                           callput: str, delta: float | None, moneyness: int | None) -> list[dict]:
    params = {
        "apiKey": api_key,
        "symbol": symbol,
        "startingDate": date,
        "dte": dte,
        "callPut": callput,
    }
    if moneyness is not None:
        params["moneyness"] = moneyness
    else:
        # delta sign normalization
        if callput == "P" and delta is not None and delta > 0:
            delta = -abs(delta)
        if callput == "C" and delta is not None and delta < 0:
            delta = abs(delta)
        params["delta"] = delta

    url = f"{base_url}/equities/eod/nearest-option-tickers"
    r = requests.get(url, params=params, timeout=60)
    print("NEAREST URL:", sanitize_url(r.url))
    print("NEAREST HTTP:", r.status_code)

    j = r.json()
    status = j.get("status", {})
    data = j.get("data", []) or []
    print("NEAREST STATUS:", status)
    print("NEAREST ROWS:", len(data))

    # Dedup by option_id
    seen = set()
    out = []
    for row in data:
        oid = row.get("option_id")
        if oid is None:
            continue
        if oid in seen:
            continue
        seen.add(oid)
        out.append(row)

    print("UNIQUE option_ids:", len(out))
    return out


def get_rawiv_for_option_id(base_url: str, api_key: str, option_id: int, date: str) -> dict | None:
    url = f"{base_url}/equities/eod/single-stock-option-raw-iv"
    params = {"apiKey": api_key, "optionId": option_id, "from": date, "to": date}
    r = requests.get(url, params=params, timeout=60)
    print("  RAWIV URL:", sanitize_url(r.url))
    print("  RAWIV HTTP:", r.status_code)

    j = r.json()
    status = j.get("status", {})
    data = j.get("data", []) or []
    print("  RAWIV STATUS:", status)
    print("  RAWIV ROWS:", len(data))

    if r.status_code == 200 and len(data) > 0:
        return data[0]
    return None


# -------------------------
# DB actions
# -------------------------
def ensure_underlying(conn, tbl_underlying, symbol: str) -> int | None:
    """
    Ensure symbol exists in dim_underlying and return its id.
    Tries common id column names.
    """
    id_col = detect_col(tbl_underlying, ["underlying_id", "id"])
    sym_col = detect_col(tbl_underlying, ["symbol", "ticker"])
    if not sym_col:
        raise RuntimeError("dim_underlying must have a symbol/ticker column.")

    if not id_col:
        upsert_one(conn, tbl_underlying, {sym_col: symbol}, conflict_cols=[sym_col])
        return None

    existing = conn.execute(
        select(tbl_underlying.c[id_col]).where(tbl_underlying.c[sym_col] == symbol).limit(1)
    ).scalar_one_or_none()

    if existing is not None:
        return int(existing)

    upsert_one(conn, tbl_underlying, {sym_col: symbol}, conflict_cols=[sym_col])

    existing = conn.execute(
        select(tbl_underlying.c[id_col]).where(tbl_underlying.c[sym_col] == symbol).limit(1)
    ).scalar_one()
    return int(existing)


def main():
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(src))

    env_path = load_env()

    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--dte", required=True, type=int)
    p.add_argument("--callput", required=True, choices=["C", "P"])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--delta", type=float)
    g.add_argument("--moneyness", type=int)
    args = p.parse_args()

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
        raise RuntimeError(
            "Missing DB URL env var. Set one of: DATABASE_URL, DB_URL, SQLALCHEMY_DATABASE_URL, POSTGRES_URL, PG_DSN"
        )

    print(f".env loaded: {env_path} (exists={bool(env_path and env_path.exists())})")
    print("BASE:", base_url)
    print("DB_URL var found: YES")
    print("")

    os.environ["DB_URL"] = db_url
    engine = get_engine()

    md = MetaData()
    md.reflect(bind=engine, only=[
        "dim_underlying",
        "dim_option_contract",
        "fact_option_eod",
        "fact_nearest_selection",
    ])

    tbl_underlying = md.tables.get("dim_underlying")
    tbl_contract = md.tables.get("dim_option_contract")
    tbl_fact = md.tables.get("fact_option_eod")
    tbl_sel = md.tables.get("fact_nearest_selection")

    if tbl_underlying is None or tbl_contract is None or tbl_fact is None or tbl_sel is None:
        print("Reflected tables:", sorted(md.tables.keys()))
        raise RuntimeError(
            "Could not reflect required tables. Expected: dim_underlying, dim_option_contract, fact_option_eod, fact_nearest_selection"
        )

    # Detect important column names
    contract_oid_col = detect_col(tbl_contract, ["option_id", "optionId"])
    if not contract_oid_col:
        raise RuntimeError("dim_option_contract must have option_id column (option_id).")

    fact_oid_col = detect_col(tbl_fact, ["option_id", "optionId"])
    if not fact_oid_col:
        raise RuntimeError("fact_option_eod must have option_id column (option_id).")

    fact_date_col = detect_col(tbl_fact, ["trade_date", "date", "asof_date"])
    if not fact_date_col:
        raise RuntimeError("fact_option_eod must have a date column (trade_date/date/asof_date).")

    contract_conflict = [contract_oid_col]
    fact_conflict = [fact_oid_col, fact_date_col]

    # Selection table cols
    sel_symbol_col = detect_col(tbl_sel, ["symbol"])
    sel_date_col = detect_col(tbl_sel, ["trade_date", "date"])
    sel_cp_col = detect_col(tbl_sel, ["call_put", "callput"])
    sel_tdte_col = detect_col(tbl_sel, ["target_dte"])
    sel_tdelta_col = detect_col(tbl_sel, ["target_delta"])
    sel_tmon_col = detect_col(tbl_sel, ["target_moneyness"])
    sel_oid_col = detect_col(tbl_sel, ["option_id"])

    missing = [n for n, c in [
        ("symbol", sel_symbol_col),
        ("trade_date", sel_date_col),
        ("call_put", sel_cp_col),
        ("target_dte", sel_tdte_col),
        ("target_delta", sel_tdelta_col),
        ("target_moneyness", sel_tmon_col),
        ("option_id", sel_oid_col),
    ] if c is None]
    if missing:
        raise RuntimeError(f"fact_nearest_selection missing columns: {missing}")

    sel_conflict = [sel_symbol_col, sel_date_col, sel_cp_col, sel_tdte_col, sel_oid_col]

    run_date = args.date
    run_date_obj = parse_iso_date(run_date)

    nearest_rows = get_nearest_option_ids(
        base_url=base_url,
        api_key=api_key,
        symbol=args.symbol,
        date=run_date,
        dte=args.dte,
        callput=args.callput,
        delta=args.delta if hasattr(args, "delta") else None,
        moneyness=args.moneyness if hasattr(args, "moneyness") else None,
    )

    ok = 0
    failed = 0

    with engine.begin() as conn:
        underlying_id = ensure_underlying(conn, tbl_underlying, args.symbol)

        for row in nearest_rows:
            option_id = int(row["option_id"])
            print(f"\n=== option_id {option_id} ===")

            # Log nearest selection
            sel_values = {
                sel_symbol_col: args.symbol,
                sel_date_col: run_date_obj,
                sel_cp_col: args.callput,
                sel_tdte_col: args.dte,
                sel_tdelta_col: args.delta if hasattr(args, "delta") else None,
                sel_tmon_col: args.moneyness if hasattr(args, "moneyness") else None,
                sel_oid_col: option_id,
            }
            sel_values = {k: v for k, v in sel_values.items() if k is not None}
            upsert_one(conn, tbl_sel, sel_values, conflict_cols=sel_conflict)

            raw = get_rawiv_for_option_id(base_url, api_key, option_id, run_date)
            if not raw:
                failed += 1
                print("  FAILED: no raw-iv row returned")
                continue

            # dim_option_contract upsert (include expiration_date if required)
            exp_col = detect_col(tbl_contract, ["expiration_date", "expiration", "expiry", "expirationDate"])
            exp_val = coerce_date(raw.get("expiration") or raw.get("expiration_date"))

            contract_values = {
                contract_oid_col: option_id,
                detect_col(tbl_contract, ["underlying_id"]): underlying_id,
                detect_col(tbl_contract, ["symbol"]): raw.get("symbol"),
                detect_col(tbl_contract, ["exchange"]): raw.get("exchange"),
                detect_col(tbl_contract, ["option_symbol", "option symbol"]): raw.get("option symbol"),
                exp_col: exp_val,
                detect_col(tbl_contract, ["strike"]): raw.get("strike"),
                detect_col(tbl_contract, ["call_put", "callput", "Call/Put"]): raw.get("Call/Put"),
                detect_col(tbl_contract, ["style"]): raw.get("style"),
            }
            contract_values = {k: v for k, v in contract_values.items() if k is not None}
            upsert_one(conn, tbl_contract, contract_values, conflict_cols=contract_conflict)

            # fact_option_eod upsert
            fact_values = {
                fact_oid_col: option_id,
                fact_date_col: run_date_obj,
                detect_col(tbl_fact, ["bid"]): raw.get("bid"),
                detect_col(tbl_fact, ["ask"]): raw.get("ask"),
                detect_col(tbl_fact, ["price", "mid"]): raw.get("price"),
                detect_col(tbl_fact, ["iv"]): raw.get("iv"),
                detect_col(tbl_fact, ["preiv"]): raw.get("preiv"),
                detect_col(tbl_fact, ["delta"]): raw.get("delta"),
                detect_col(tbl_fact, ["gamma"]): raw.get("gamma"),
                detect_col(tbl_fact, ["vega"]): raw.get("vega"),
                detect_col(tbl_fact, ["theta"]): raw.get("theta"),
                detect_col(tbl_fact, ["rho"]): raw.get("rho"),
                detect_col(tbl_fact, ["volume"]): raw.get("volume"),
                detect_col(tbl_fact, ["open_interest", "open interest"]): raw.get("open interest"),
                detect_col(tbl_fact, ["is_settlement"]): raw.get("is_settlement"),
                detect_col(tbl_fact, ["underlying_close_adj", "adj_close", "adjusted_close"]): raw.get("Adjusted close"),
                detect_col(tbl_fact, ["underlying_close_unadj", "unadj_close", "unadjusted_close"]): raw.get("Unadjusted close"),
            }
            fact_values = {k: v for k, v in fact_values.items() if k is not None}
            upsert_one(conn, tbl_fact, fact_values, conflict_cols=fact_conflict)

            ok += 1
            print(
                "  DB OK:",
                raw.get("option symbol"),
                "exp=", raw.get("expiration"),
                "K=", raw.get("strike"),
                "CP=", raw.get("Call/Put"),
                "iv=", raw.get("iv"),
                "delta=", raw.get("delta"),
            )

        # Verification counts
        count = conn.execute(
            select(tbl_fact.c[fact_oid_col]).where(tbl_fact.c[fact_date_col] == run_date_obj)
        ).all()
        print("\n--- SUMMARY ---")
        print("raw-iv ok:", ok, "failed:", failed)
        print(f"fact_option_eod rows on {run_date}:", len(count))


if __name__ == "__main__":
    main()
