from __future__ import annotations

import datetime as dt
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Engine

from config import Settings
from db.engine import get_engine
from ivol.client import IvolClient


# ----------------------------
# Utils
# ----------------------------

def pretty(obj: Any, limit: int = 2400) -> str:
    try:
        return json.dumps(obj, indent=2)[:limit]
    except Exception:
        return str(obj)[:limit]


def http_get(base: str, path: str, params: Dict[str, Any], timeout: int = 180) -> requests.Response:
    url = base.rstrip("/") + "/" + path.lstrip("/")
    return requests.get(url, params=params, timeout=timeout)


def extract_data_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def pick_option_id(row: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    oid = row.get("option_id") or row.get("optionId") or row.get("id")
    sym = row.get("option_symbol") or row.get("option symbol") or row.get("symbol")
    try:
        oid_int = int(oid) if oid is not None else None
    except Exception:
        oid_int = None
    return oid_int, sym if isinstance(sym, str) else None


def poll_if_pending(first_payload: Dict[str, Any], max_polls: int = 25, sleep_s: float = 2.0) -> Dict[str, Any]:
    st = first_payload.get("status") or {}
    code = str(st.get("code") or "").upper()
    details_url = st.get("urlForDetails")
    if code != "PENDING" or not details_url:
        return first_payload

    print("\nPENDING → polling urlForDetails...")
    last_payload = first_payload
    for i in range(1, max_polls + 1):
        rr = requests.get(details_url, timeout=180)
        if rr.status_code != 200:
            print(f" poll {i}/{max_polls}: HTTP {rr.status_code}")
            time.sleep(sleep_s)
            continue
        try:
            last_payload = rr.json()
        except Exception:
            print(f" poll {i}/{max_polls}: non-JSON response")
            time.sleep(sleep_s)
            continue

        st2 = last_payload.get("status") or {}
        code2 = str(st2.get("code") or "").upper()
        print(f" poll {i}/{max_polls}: status={code2} recordsFound={st2.get('recordsFound')}")
        if code2 != "PENDING":
            break
        time.sleep(sleep_s)

    return last_payload


def to_date(v: Any) -> Optional[dt.date]:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        return dt.date.fromisoformat(v.strip()[:10])
    return None


# ----------------------------
# API steps
# ----------------------------

def sanity_stock_prices(base: str, auth_variants: List[Dict[str, Any]], symbol: str, date: str, region: str) -> Dict[str, Any]:
    print("\n=== Sanity: /equities/eod/stock-prices ===")
    for auth in auth_variants:
        params = {**auth, "symbol": symbol, "from": date, "to": date, "region": region}
        r = http_get(base, "/equities/eod/stock-prices", params)
        print(" auth keys:", list(auth.keys()), "HTTP", r.status_code)
        if r.status_code == 200:
            print(pretty(r.json()))
            return auth
        print(r.text[:500])
    raise SystemExit("stock-prices failed for all auth variants")


def find_option_id(base: str, auth: Dict[str, Any], symbol: str, starting_date: str) -> Tuple[int, str]:
    print("\n=== Find option_id via /equities/eod/nearest-option-tickers ===")

    dtes = [7, 14, 21, 30, 45, 60, 90]
    moneyness_list = [0, 5, 10]
    deltas_by_cp = {"C": [0.50, 0.25, 0.10], "P": [-0.50, -0.25, -0.10]}

    for cp in ["C", "P"]:
        for dte in dtes:
            # moneyness first
            for m in moneyness_list:
                params = {**auth, "symbol": symbol, "startingDate": starting_date, "dte": dte, "moneyness": m, "callPut": cp}
                r = http_get(base, "/equities/eod/nearest-option-tickers", params)
                if r.status_code != 200:
                    continue
                rows = extract_data_list(r.json())
                if rows:
                    oid, osym = pick_option_id(rows[0])
                    if oid:
                        print(f"\nFOUND ✅ callPut={cp} dte={dte} moneyness={m} (rows={len(rows)})")
                        print(" first row keys:", list(rows[0].keys()))
                        return oid, (osym or "")

            # delta fallback
            for d in deltas_by_cp[cp]:
                params = {**auth, "symbol": symbol, "startingDate": starting_date, "dte": dte, "delta": d, "callPut": cp}
                r = http_get(base, "/equities/eod/nearest-option-tickers", params)
                if r.status_code != 200:
                    continue
                rows = extract_data_list(r.json())
                if rows:
                    oid, osym = pick_option_id(rows[0])
                    if oid:
                        print(f"\nFOUND ✅ callPut={cp} dte={dte} delta={d} (rows={len(rows)})")
                        print(" first row keys:", list(rows[0].keys()))
                        return oid, (osym or "")
    raise SystemExit("No option_id found (0 rows for all combinations).")


def fetch_single_stock_option_raw_iv(base: str, auth: Dict[str, Any], option_id: int, date: str) -> Dict[str, Any]:
    print("\n=== Test: /equities/eod/single-stock-option-raw-iv ===")
    params = {**auth, "optionId": option_id, "from": date, "to": date}
    r = http_get(base, "/equities/eod/single-stock-option-raw-iv", params)
    print("HTTP", r.status_code)
    if r.status_code != 200:
        print(r.text[:2000])
        raise SystemExit("single-stock-option-raw-iv failed.")
    payload = poll_if_pending(r.json())
    print(pretty(payload))
    return payload


# ----------------------------
# DB write (MySQL-safe, no RETURNING)
# ----------------------------

def get_or_create_underlying(engine: Engine, t_under: Table, symbol: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(select(t_under.c.underlying_id).where(t_under.c.symbol == symbol)).fetchone()
        if row:
            return int(row[0])

        res = conn.execute(t_under.insert().values(symbol=symbol))
        # MySQL usually returns inserted PK here
        pk = None
        try:
            if res.inserted_primary_key:
                pk = res.inserted_primary_key[0]
        except Exception:
            pk = None

        if pk is not None:
            return int(pk)

        # fallback: re-select
        row2 = conn.execute(select(t_under.c.underlying_id).where(t_under.c.symbol == symbol)).fetchone()
        if not row2:
            raise RuntimeError("Failed to insert/select dim_underlying")
        return int(row2[0])


def get_or_create_option_contract(engine: Engine, t_opt: Table, underlying_id: int, option_id: int, option_symbol: str, raw_row: Dict[str, Any]) -> int:
    expiration = to_date(raw_row.get("expiration"))
    if expiration is None:
        raise RuntimeError("expiration_date required but API row missing expiration")

    strike = raw_row.get("strike")
    call_put = raw_row.get("Call/Put")
    style = raw_row.get("style")

    with engine.begin() as conn:
        row = conn.execute(select(t_opt.c.option_id).where(t_opt.c.option_id == option_id)).fetchone()
        if row:
            return int(option_id)

        conn.execute(
            t_opt.insert().values(
                option_id=option_id,
                underlying_id=underlying_id,
                expiration_date=expiration,
                strike=strike,
                call_put=call_put,
                style=style,
                option_symbol=option_symbol,
            )
        )
        return int(option_id)


def upsert_fact_option_eod(engine: Engine, t_fact: Table, option_id: int, raw_row: Dict[str, Any]) -> None:
    trade_date = to_date(raw_row.get("date"))
    if trade_date is None:
        raise RuntimeError("raw_row['date'] missing/unparseable")

    values = dict(
        option_id=option_id,
        trade_date=trade_date,
        bid=raw_row.get("bid"),
        ask=raw_row.get("ask"),
        price=raw_row.get("price"),
        iv=raw_row.get("iv"),
        preiv=raw_row.get("preiv"),
        delta=raw_row.get("delta"),
        gamma=raw_row.get("gamma"),
        vega=raw_row.get("vega"),
        theta=raw_row.get("theta"),
        rho=raw_row.get("rho"),
        volume=raw_row.get("volume"),
        open_interest=raw_row.get("open interest"),
        is_settlement=raw_row.get("is_settlement"),
    )

    with engine.begin() as conn:
        where = (t_fact.c.option_id == option_id) & (t_fact.c.trade_date == trade_date)
        exists = conn.execute(select(t_fact.c.option_id).where(where)).fetchone()
        if exists:
            conn.execute(t_fact.update().where(where).values(**values))
            print("DB: updated fact_option_eod for", trade_date.isoformat())
        else:
            conn.execute(t_fact.insert().values(**values))
            print("DB: inserted fact_option_eod for", trade_date.isoformat())


def write_single_option_to_db(db_url: str, underlying: str, option_id: int, option_symbol: str, raw_row: Dict[str, Any]) -> None:
    os.environ["DB_URL"] = db_url
    engine = get_engine()
    md = MetaData()
    md.reflect(bind=engine, only=["dim_underlying", "dim_option_contract", "fact_option_eod"])

    t_under = md.tables["dim_underlying"]
    t_opt = md.tables["dim_option_contract"]
    t_fact = md.tables["fact_option_eod"]

    underlying_id = get_or_create_underlying(engine, t_under, underlying)
    _ = get_or_create_option_contract(engine, t_opt, underlying_id, option_id, option_symbol, raw_row)
    upsert_fact_option_eod(engine, t_fact, option_id, raw_row)

    print("\nDB OK ✅")
    print(" underlying_id:", underlying_id)
    print(" option_id:", option_id)
    print(" option_symbol:", option_symbol)


# ----------------------------
# main
# ----------------------------

def main() -> None:
    load_dotenv()
    s = Settings()

    api_key = os.getenv("IVOL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing IVOL_API_KEY in .env")

    if not getattr(s, "db_url", None):
        raise SystemExit("Settings.db_url is missing (DB_URL in .env).")

    base = s.ivol_base_url.replace("http://", "https://").rstrip("/")
    underlying = "AAPL"
    date = "2022-06-30"

    print("Base:", base)
    print("Underlying:", underlying, "Date:", date)

    token = IvolClient(s).get_token()
    auth_variants = [
        {"apiKey": api_key},
        {"token": token},
        {"username": s.ivol_username, "password": s.ivol_password},
    ]

    working_auth = sanity_stock_prices(base, auth_variants, underlying, date, s.region)

    option_id, option_symbol = find_option_id(base, working_auth, underlying, date)
    print("\nPicked option_id:", option_id)
    print("Picked option_symbol:", option_symbol)

    payload = fetch_single_stock_option_raw_iv(base, working_auth, option_id, date)
    rows = extract_data_list(payload)
    if not rows:
        raise SystemExit("single-stock-option-raw-iv returned 0 rows unexpectedly")
    raw_row = rows[0]

    write_single_option_to_db(s.db_url, underlying, option_id, option_symbol, raw_row)


if __name__ == "__main__":
    main()
