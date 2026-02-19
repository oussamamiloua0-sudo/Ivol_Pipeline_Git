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


def sanity_stock_prices(base: str, auth: Dict[str, Any], symbol: str, date: str, region: str) -> None:
    params = {**auth, "symbol": symbol, "from": date, "to": date, "region": region}
    r = http_get(base, "/equities/eod/stock-prices", params)
    if r.status_code != 200:
        raise SystemExit(f"stock-prices failed: HTTP {r.status_code} {r.text[:200]}")
    print("\nStock OK ✅")


def nearest_options(base: str, auth: Dict[str, Any], symbol: str, date: str) -> List[Tuple[int, str, str]]:
    """
    Return a small list of (option_id, option_symbol, comment) across a few combos.
    We keep it tiny on purpose: 2 call + 2 put, 7DTE ATM.
    """
    found: List[Tuple[int, str, str]] = []

    combos = [
        ("C", 7, 0),
        ("C", 7, 5),
        ("P", 7, 0),
        ("P", 7, 5),
    ]

    for cp, dte, m in combos:
        params = {**auth, "symbol": symbol, "startingDate": date, "dte": dte, "moneyness": m, "callPut": cp}
        r = http_get(base, "/equities/eod/nearest-option-tickers", params)
        if r.status_code != 200:
            print(f"nearest-option-tickers failed cp={cp} dte={dte} m={m}: HTTP {r.status_code}")
            continue
        rows = extract_data_list(r.json())
        if not rows:
            print(f"0 rows for cp={cp} dte={dte} m={m}")
            continue
        oid, osym = pick_option_id(rows[0])
        if oid and osym:
            found.append((oid, osym, str(rows[0].get("comment") or "")))
            print(f"FOUND {cp} dte={dte} m={m}: option_id={oid} symbol={osym}")

    # unique by option_id
    uniq: Dict[int, Tuple[int, str, str]] = {x[0]: x for x in found}
    return list(uniq.values())


def fetch_raw_iv_row(base: str, auth: Dict[str, Any], option_id: int, date: str) -> Optional[Dict[str, Any]]:
    params = {**auth, "optionId": option_id, "from": date, "to": date}
    r = http_get(base, "/equities/eod/single-stock-option-raw-iv", params)
    if r.status_code != 200:
        print(f"raw-iv failed option_id={option_id}: HTTP {r.status_code}")
        return None
    payload = poll_if_pending(r.json())
    rows = extract_data_list(payload)
    return rows[0] if rows else None


def get_or_create_underlying(engine: Engine, t_under: Table, symbol: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(select(t_under.c.underlying_id).where(t_under.c.symbol == symbol)).fetchone()
        if row:
            return int(row[0])
        res = conn.execute(t_under.insert().values(symbol=symbol))
        pk = None
        try:
            if res.inserted_primary_key:
                pk = res.inserted_primary_key[0]
        except Exception:
            pk = None
        if pk is not None:
            return int(pk)
        row2 = conn.execute(select(t_under.c.underlying_id).where(t_under.c.symbol == symbol)).fetchone()
        if not row2:
            raise RuntimeError("Failed to insert/select dim_underlying")
        return int(row2[0])


def get_or_create_option_contract(engine: Engine, t_opt: Table, underlying_id: int, option_id: int, option_symbol: str, raw_row: Dict[str, Any]) -> int:
    expiration = to_date(raw_row.get("expiration"))
    if expiration is None:
        raise RuntimeError("expiration_date required but API row missing expiration")

    with engine.begin() as conn:
        row = conn.execute(select(t_opt.c.option_id).where(t_opt.c.option_id == option_id)).fetchone()
        if row:
            return int(option_id)

        conn.execute(
            t_opt.insert().values(
                option_id=option_id,
                underlying_id=underlying_id,
                expiration_date=expiration,
                strike=raw_row.get("strike"),
                call_put=raw_row.get("Call/Put"),
                style=raw_row.get("style"),
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
        else:
            conn.execute(t_fact.insert().values(**values))


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
    auth = {"apiKey": api_key}  # we know apiKey works

    sanity_stock_prices(base, auth, underlying, date, s.region)

    candidates = nearest_options(base, auth, underlying, date)
    if not candidates:
        raise SystemExit("No nearest options found for slice combos.")

    os.environ["DB_URL"] = s.db_url
    engine = get_engine()
    md = MetaData()
    md.reflect(bind=engine, only=["dim_underlying", "dim_option_contract", "fact_option_eod"])

    t_under = md.tables["dim_underlying"]
    t_opt = md.tables["dim_option_contract"]
    t_fact = md.tables["fact_option_eod"]

    underlying_id = get_or_create_underlying(engine, t_under, underlying)

    inserted = 0
    for oid, osym, _comment in candidates:
        row = fetch_raw_iv_row(base, auth, oid, date)
        if not row:
            continue
        get_or_create_option_contract(engine, t_opt, underlying_id, oid, osym, row)
        upsert_fact_option_eod(engine, t_fact, oid, row)
        inserted += 1
        print(f"DB wrote ✅ option_id={oid} symbol={osym}")

    print(f"\nDONE ✅ wrote {inserted}/{len(candidates)} option rows into MySQL for {date}")


if __name__ == "__main__":
    main()
