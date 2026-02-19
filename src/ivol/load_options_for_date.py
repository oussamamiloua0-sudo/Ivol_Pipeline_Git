from __future__ import annotations

import argparse
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
# Helpers
# ----------------------------

def pretty(obj: Any, limit: int = 1200) -> str:
    try:
        return json.dumps(obj, indent=2)[:limit]
    except Exception:
        return str(obj)[:limit]


def http_get(base: str, path: str, params: Dict[str, Any], timeout: int = 180) -> requests.Response:
    url = base.rstrip("/") + "/" + path.lstrip("/")
    return requests.get(url, params=params, timeout=timeout)


def extract_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    d = payload.get("data")
    return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []


def pick_option(row: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
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

    for _ in range(max_polls):
        rr = requests.get(details_url, timeout=180)
        if rr.status_code != 200:
            time.sleep(sleep_s)
            continue
        try:
            payload = rr.json()
        except Exception:
            time.sleep(sleep_s)
            continue
        st2 = payload.get("status") or {}
        if str(st2.get("code") or "").upper() != "PENDING":
            return payload
        time.sleep(sleep_s)

    return first_payload


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
# DB ops (MySQL-safe)
# ----------------------------

def get_or_create_underlying(engine: Engine, t: Table, symbol: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(select(t.c.underlying_id).where(t.c.symbol == symbol)).fetchone()
        if row:
            return int(row[0])
        res = conn.execute(t.insert().values(symbol=symbol))
        try:
            if res.inserted_primary_key:
                return int(res.inserted_primary_key[0])
        except Exception:
            pass
        row2 = conn.execute(select(t.c.underlying_id).where(t.c.symbol == symbol)).fetchone()
        if not row2:
            raise RuntimeError("Failed to insert/select dim_underlying")
        return int(row2[0])


def get_or_create_option_contract(engine: Engine, t: Table, underlying_id: int, option_id: int, option_symbol: str, raw: Dict[str, Any]) -> int:
    exp = to_date(raw.get("expiration"))
    if exp is None:
        raise RuntimeError("API row missing expiration")

    with engine.begin() as conn:
        row = conn.execute(select(t.c.option_id).where(t.c.option_id == option_id)).fetchone()
        if row:
            return int(option_id)

        conn.execute(
            t.insert().values(
                option_id=option_id,
                underlying_id=underlying_id,
                expiration_date=exp,
                strike=raw.get("strike"),
                call_put=raw.get("Call/Put"),
                style=raw.get("style"),
                option_symbol=option_symbol,
            )
        )
        return int(option_id)


def upsert_fact_option_eod(engine: Engine, t: Table, option_id: int, raw: Dict[str, Any]) -> None:
    trade_date = to_date(raw.get("date"))
    if trade_date is None:
        raise RuntimeError("API row missing date")

    values = dict(
        option_id=option_id,
        trade_date=trade_date,
        bid=raw.get("bid"),
        ask=raw.get("ask"),
        price=raw.get("price"),
        iv=raw.get("iv"),
        preiv=raw.get("preiv"),
        delta=raw.get("delta"),
        gamma=raw.get("gamma"),
        vega=raw.get("vega"),
        theta=raw.get("theta"),
        rho=raw.get("rho"),
        volume=raw.get("volume"),
        open_interest=raw.get("open interest"),
        is_settlement=raw.get("is_settlement"),
    )

    with engine.begin() as conn:
        where = (t.c.option_id == option_id) & (t.c.trade_date == trade_date)
        exists = conn.execute(select(t.c.option_id).where(where)).fetchone()
        if exists:
            conn.execute(t.update().where(where).values(**values))
        else:
            conn.execute(t.insert().values(**values))


# ----------------------------
# Loader
# ----------------------------

def nearest_option_candidates(base: str, auth: Dict[str, Any], symbol: str, date: str,
                              targets: List[Tuple[str, int, str, float]]) -> List[Tuple[int, str, Tuple[str, int, str, float]]]:
    """
    targets: list of tuples (callPut, dte, mode, value)
      mode='m' => moneyness=value (int-ish), mode='d' => delta=value (signed)
    """
    found: List[Tuple[int, str, Tuple[str, int, str, float]]] = []
    for cp, dte, mode, val in targets:
        params = {**auth, "symbol": symbol, "startingDate": date, "dte": dte, "callPut": cp}
        if mode == "m":
            params["moneyness"] = int(val)
        else:
            params["delta"] = float(val)

        r = http_get(base, "/equities/eod/nearest-option-tickers", params)
        if r.status_code != 200:
            print(f"nearest-option-tickers failed target={cp,dte,mode,val}: HTTP {r.status_code}")
            continue
        rows = extract_rows(r.json())
        if not rows:
            print(f"0 rows target={cp,dte,mode,val}")
            continue
        oid, osym = pick_option(rows[0])
        if oid and osym:
            found.append((oid, osym, (cp, dte, mode, val)))
            print(f"FOUND target={cp,dte,mode,val} -> {oid} {osym}")

    # unique by option_id
    uniq: Dict[int, Tuple[int, str, Tuple[str, int, str, float]]] = {x[0]: x for x in found}
    return list(uniq.values())


def fetch_raw_iv(base: str, auth: Dict[str, Any], option_id: int, date: str) -> Optional[Dict[str, Any]]:
    params = {**auth, "optionId": option_id, "from": date, "to": date}
    r = http_get(base, "/equities/eod/single-stock-option-raw-iv", params)
    if r.status_code != 200:
        print(f"raw-iv failed option_id={option_id}: HTTP {r.status_code}")
        return None
    payload = poll_if_pending(r.json())
    rows = extract_rows(payload)
    return rows[0] if rows else None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Load a small set of options for one symbol+date into MySQL.")
    p.add_argument("--symbol", default="AAPL")
    p.add_argument("--date", default="2022-06-30")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    s = Settings()

    api_key = os.getenv("IVOL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing IVOL_API_KEY in .env")
    if not getattr(s, "db_url", None):
        raise SystemExit("Missing DB_URL in .env")

    args = parse_args()

    base = s.ivol_base_url.replace("http://", "https://").rstrip("/")
    symbol = args.symbol
    date = args.date

    print("Base:", base)
    print("Symbol:", symbol, "Date:", date)

    # known-good auth for your plan
    auth = {"apiKey": api_key}

    # Keep targets small and explicit (you can add later)
    targets: List[Tuple[str, int, str, float]] = [
        ("C", 7, "m", 0),
        ("C", 7, "m", 5),
        ("P", 7, "m", 0),
        ("P", 7, "m", 5),
    ]

    candidates = nearest_option_candidates(base, auth, symbol, date, targets)
    if not candidates:
        raise SystemExit("No candidates returned from nearest-option-tickers.")

    os.environ["DB_URL"] = s.db_url
    engine = get_engine()
    md = MetaData()
    md.reflect(bind=engine, only=["dim_underlying", "dim_option_contract", "fact_option_eod"])
    t_under = md.tables["dim_underlying"]
    t_opt = md.tables["dim_option_contract"]
    t_fact = md.tables["fact_option_eod"]

    underlying_id = get_or_create_underlying(engine, t_under, symbol)

    wrote = 0
    for oid, osym, target in candidates:
        raw = fetch_raw_iv(base, auth, oid, date)
        if not raw:
            continue
        get_or_create_option_contract(engine, t_opt, underlying_id, oid, osym, raw)
        upsert_fact_option_eod(engine, t_fact, oid, raw)
        wrote += 1
        print(f"DB wrote ✅ option_id={oid} target={target}")

    print(f"\nDONE ✅ wrote {wrote}/{len(candidates)} option rows into MySQL for {symbol} {date}")


if __name__ == "__main__":
    main()
