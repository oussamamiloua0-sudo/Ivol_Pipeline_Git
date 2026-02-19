"""
Reusable single-day loader:
- --symbol + --date (YYYY-MM-DD)
- Loads:
  1) underlying EOD (stock-prices) -> fact_underlying_eod
  2) ivx + hv -> fact_vol_metrics
  3) small option slice:
     nearest-option-tickers -> option_ids
     single-stock-option-raw-iv -> dim_option_contract + fact_option_eod
- MySQL-safe idempotent upserts (no RETURNING), SQLAlchemy 2.0 compatible

IMPORTANT FIXES:
- iVol EOD endpoints expect `date=YYYY-MM-DD` (not `tradeDate`)
- single-stock-option-raw-iv expects `from` and `to` (not `date`/`tradeDate`)
- dim_option_contract.call_put is NOT NULL -> always carry C/P from nearest-option-tickers as fallback
"""

from __future__ import annotations

import argparse
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from db.engine import get_engine

load_dotenv()

try:
    from config import Settings  # type: ignore
except Exception:
    Settings = None  # type: ignore


# ----------------------------
# Config helpers
# ----------------------------

def _get_setting(obj: Any, names: Iterable[str], default: Optional[str] = None) -> Optional[str]:
    for n in names:
        if obj is not None and hasattr(obj, n):
            v = getattr(obj, n)
            if v:
                return str(v)
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


@dataclass(frozen=True)
class AppConfig:
    base_url: str
    api_key: str
    db_url: str


def load_config() -> AppConfig:
    settings = None
    if Settings is not None:
        settings = Settings()

    base_url = _get_setting(
        settings,
        ["IVOL_BASE_URL", "ivol_base_url", "BASE_URL", "base_url"],
        "https://restapi.ivolatility.com",
    )
    api_key = _get_setting(settings, ["IVOL_API_KEY", "ivol_api_key", "API_KEY", "api_key"], None)
    db_url = _get_setting(settings, ["DB_URL", "db_url"], None)

    if not api_key:
        raise SystemExit("Missing API key. Set IVOL_API_KEY in .env (or ensure Settings() exposes it).")
    if not db_url:
        raise SystemExit("Missing DB_URL. Set DB_URL in .env (or ensure Settings() exposes it).")

    # Safety: auto-upgrade known http base to https
    if base_url.startswith("http://restapi.ivolatility.com"):
        base_url = "https://restapi.ivolatility.com"

    return AppConfig(base_url=base_url.rstrip("/"), api_key=api_key, db_url=db_url)


# ----------------------------
# Utility converters
# ----------------------------

def parse_yyyy_mm_dd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def as_float(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None


def as_int(x: Any) -> Optional[int]:
    if x is None or x == "":
        return None
    try:
        return int(float(x))
    except Exception:
        return None


def as_bool01(x: Any) -> Optional[int]:
    if x is None or x == "":
        return None
    if isinstance(x, bool):
        return 1 if x else 0
    s = str(x).strip().lower()
    if s in {"1", "true", "t", "yes", "y"}:
        return 1
    if s in {"0", "false", "f", "no", "n"}:
        return 0
    return None


def as_decimal(x: Any) -> Optional[Decimal]:
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x))
    except Exception:
        return None


def as_date(x: Any) -> Optional[date]:
    if x is None or x == "":
        return None
    if isinstance(x, date):
        return x
    s = str(x).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def filter_to_table_columns(tbl: Table, data: Dict[str, Any]) -> Dict[str, Any]:
    cols = set(c.name for c in tbl.columns)
    return {k: v for k, v in data.items() if k in cols}


# ----------------------------
# iVol API client
# ----------------------------

class IVolClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = {"Accept": "application/json", "apiKey": self.api_key}

        # Some APIs accept apiKey as query param too; harmless if ignored.
        params = dict(params)
        params.setdefault("apiKey", self.api_key)

        r = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        try:
            payload = r.json()
        except Exception:
            payload = {"_raw": r.text}

        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} for {endpoint}: {payload}")

        return payload


def extract_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload.get("result"), list):
        return payload["result"]

    for v in payload.values():
        if isinstance(v, dict):
            for kk in ("data", "results", "result"):
                if isinstance(v.get(kk), list):
                    return v[kk]
    return []


# ----------------------------
# DB upsert helpers (MySQL)
# ----------------------------

def upsert_mysql(engine, tbl: Table, pk_cols: List[str], data: Dict[str, Any]) -> None:
    data = filter_to_table_columns(tbl, data)
    stmt = mysql_insert(tbl).values(**data)

    update_cols = {}
    for k in data.keys():
        if k in pk_cols:
            continue
        if k in tbl.c:
            update_cols[k] = stmt.inserted[k]

    if update_cols:
        stmt = stmt.on_duplicate_key_update(**update_cols)

    with engine.begin() as conn:
        conn.execute(stmt)


def ensure_underlying_id(engine, md: MetaData, symbol: str) -> int:
    dim_underlying = md.tables["dim_underlying"]

    stmt = mysql_insert(dim_underlying).values(symbol=symbol)
    # no-op update (MySQL-safe)
    stmt = stmt.on_duplicate_key_update(symbol=dim_underlying.c.symbol)

    with engine.begin() as conn:
        conn.execute(stmt)
        q = select(dim_underlying.c.underlying_id).where(dim_underlying.c.symbol == symbol)
        underlying_id = conn.execute(q).scalar_one()

    return int(underlying_id)


# ----------------------------
# Loaders
# ----------------------------

def load_underlying_eod(client: IVolClient, engine, md: MetaData, symbol: str, trade_date: date, underlying_id: int) -> bool:
    fact = md.tables["fact_underlying_eod"]

    # IMPORTANT: `date` not `tradeDate`
    payload = client.get(
        "/equities/eod/stock-prices",
        {"symbol": symbol, "date": trade_date.isoformat()},
    )
    rows = extract_rows(payload)
    if not rows:
        print("WARN: stock-prices returned 0 rows.")
        return False

    r = rows[0]

    data = {
        "underlying_id": underlying_id,
        "trade_date": trade_date,

        # Common names
        "open": as_float(r.get("open")),
        "high": as_float(r.get("high")),
        "low": as_float(r.get("low")),
        "close": as_float(r.get("close")),
        "adj_close": as_float(r.get("adjClose")),

        # Alternative schema names (kept if your table has them)
        "open_price": as_float(r.get("open")),
        "high_price": as_float(r.get("high")),
        "low_price": as_float(r.get("low")),
        "close_price": as_float(r.get("close")),
        "adj_close_price": as_float(r.get("adjClose")),

        "volume": as_int(r.get("volume")),
        "dividend": as_float(r.get("dividend")),
        "split": as_float(r.get("split")),
    }

    upsert_mysql(engine, fact, pk_cols=["underlying_id", "trade_date"], data=data)
    return True


def load_vol_metrics(client: IVolClient, engine, md: MetaData, symbol: str, trade_date: date, underlying_id: int) -> bool:
    fact = md.tables["fact_vol_metrics"]

    # IMPORTANT: `date` not `tradeDate`
    ivx_payload = client.get(
        "/equities/eod/ivx",
        {"symbol": symbol, "date": trade_date.isoformat()},
    )
    ivx_rows = extract_rows(ivx_payload)

    hv_payload = client.get(
        "/equities/eod/hv",
        {"symbol": symbol, "date": trade_date.isoformat()},
    )
    hv_rows = extract_rows(hv_payload)

    if not ivx_rows and not hv_rows:
        print("WARN: ivx + hv both returned 0 rows.")
        return False

    ivx = None
    if ivx_rows:
        rr = ivx_rows[0]
        ivx = (
            as_float(rr.get("ivx"))
            or as_float(rr.get("ivx30"))
            or as_float(rr.get("ivx_30"))
            or as_float(rr.get("ivx1m"))
        )

    hv = {}
    if hv_rows:
        rr = hv_rows[0]
        hv = {
            "hv10": as_float(rr.get("hv10")),
            "hv20": as_float(rr.get("hv20")),
            "hv30": as_float(rr.get("hv30")),
            "hv60": as_float(rr.get("hv60")),
            "hv90": as_float(rr.get("hv90")),
        }

    data = {
        "underlying_id": underlying_id,
        "trade_date": trade_date,
        "ivx": ivx,
        **hv,
    }

    upsert_mysql(engine, fact, pk_cols=["underlying_id", "trade_date"], data=data)
    return True


def parse_call_put_from_option_symbol(option_symbol: Optional[str]) -> Optional[str]:
    if not option_symbol:
        return None
    # Example: "AAPL  220708C00136000" -> "C"
    m = re.search(r"\d{6}([CP])\d{8}", option_symbol)
    if m:
        return m.group(1)
    return None


def fetch_option_ids_slice(
    client: IVolClient,
    symbol: str,
    trade_date: date,
    dte: int,
    moneyness_list: List[int],
    include_calls: bool,
    include_puts: bool,
    max_contracts: int,
    sleep_s: float,
) -> List[Dict[str, Any]]:
    """
    Returns a list of dicts like:
      { "option_id": int, "call_put": "C"|"P", "option_symbol": Optional[str] }
    We carry call_put from the request (C/P) to satisfy dim_option_contract.call_put NOT NULL.
    """
    picked: List[Dict[str, Any]] = []
    seen: set[int] = set()

    combos: List[Tuple[str, int]] = []
    if include_calls:
        combos.extend([("C", m) for m in moneyness_list])
    if include_puts:
        combos.extend([("P", m) for m in moneyness_list])

    for call_put, moneyness in combos:
        if len(picked) >= max_contracts:
            break

        payload = client.get(
            "/equities/eod/nearest-option-tickers",
            {
                "symbol": symbol,
                "dte": dte,
                "callPut": call_put,
                "moneyness": moneyness,
                "startingDate": trade_date.isoformat(),
            },
        )
        rows = extract_rows(payload)
        if not rows:
            print(f"INFO: nearest-option-tickers 0 rows for {call_put} moneyness={moneyness} dte={dte}")
            time.sleep(sleep_s)
            continue

        r = rows[0]
        raw_id = r.get("optionId") or r.get("option_id") or r.get("optionID") or r.get("optionid")
        oid = as_int(raw_id)
        if oid is None:
            print(f"WARN: could not parse option id from nearest-option-tickers row: {r}")
            time.sleep(sleep_s)
            continue

        oid = int(oid)
        if oid in seen:
            time.sleep(sleep_s)
            continue

        opt_sym = r.get("optionSymbol") or r.get("option_symbol") or r.get("ticker") or r.get("optionTicker")

        seen.add(oid)
        picked.append({"option_id": oid, "call_put": call_put, "option_symbol": opt_sym})
        print(f"OK: slice add option_id={oid} ({call_put} moneyness={moneyness} dte={dte})")

        time.sleep(sleep_s)

    return picked


def load_options_for_date(
    client: IVolClient,
    engine,
    md: MetaData,
    symbol: str,
    trade_date: date,
    underlying_id: int,
    dte: int,
    moneyness_list: List[int],
    include_calls: bool,
    include_puts: bool,
    max_contracts: int,
    sleep_s: float,
) -> int:
    dim_contract = md.tables["dim_option_contract"]
    fact_opt = md.tables["fact_option_eod"]

    option_reqs = fetch_option_ids_slice(
        client=client,
        symbol=symbol,
        trade_date=trade_date,
        dte=dte,
        moneyness_list=moneyness_list,
        include_calls=include_calls,
        include_puts=include_puts,
        max_contracts=max_contracts,
        sleep_s=sleep_s,
    )

    wrote = 0
    for req in option_reqs:
        oid = int(req["option_id"])
        cp_fallback = (req.get("call_put") or "").upper()   # "C" or "P"
        optsym_fallback = req.get("option_symbol")

        # IMPORTANT: single-stock-option-raw-iv expects from/to
        payload = client.get(
            "/equities/eod/single-stock-option-raw-iv",
            {
                "symbol": symbol,
                "optionId": oid,
                "from": trade_date.isoformat(),
                "to": trade_date.isoformat(),
            },
        )
        rows = extract_rows(payload)

        # Defensive retry with alternate param name if API expects it
        if not rows:
            payload = client.get(
                "/equities/eod/single-stock-option-raw-iv",
                {
                    "symbol": symbol,
                    "option_id": oid,
                    "from": trade_date.isoformat(),
                    "to": trade_date.isoformat(),
                },
            )
            rows = extract_rows(payload)

        if not rows:
            print(f"WARN: single-stock-option-raw-iv returned 0 rows for option_id={oid}")
            time.sleep(sleep_s)
            continue

        r = rows[0]

        option_symbol = r.get("optionSymbol") or r.get("option_symbol") or optsym_fallback
        expiration = as_date(r.get("expirationDate") or r.get("expiration") or r.get("expDate"))
        strike = as_decimal(r.get("strike"))

        call_put = (
            r.get("callPut")
            or r.get("call_put")
            or parse_call_put_from_option_symbol(option_symbol)
            or cp_fallback
        )
        call_put = str(call_put).upper() if call_put else None

        style = r.get("style") or r.get("exerciseStyle") or r.get("optionStyle")

        # Upsert dim_option_contract (call_put must NOT be NULL)
        contract_row = {
            "option_id": oid,
            "underlying_id": underlying_id,
            "expiration_date": expiration,
            "strike": strike,
            "call_put": call_put,
            "style": style,
            "option_symbol": option_symbol,
        }
        upsert_mysql(engine, dim_contract, pk_cols=["option_id"], data=contract_row)

        # Upsert fact_option_eod
        opt_row = {
            "option_id": oid,
            "trade_date": trade_date,

            "bid": as_float(r.get("bid")),
            "ask": as_float(r.get("ask")),
            "price": as_float(r.get("price") or r.get("mid") or r.get("mark") or r.get("last")),

            "iv": as_float(r.get("iv") or r.get("IV")),
            "preiv": as_float(r.get("preIv") or r.get("preIV") or r.get("preiv")),

            "delta": as_float(r.get("delta")),
            "gamma": as_float(r.get("gamma")),
            "theta": as_float(r.get("theta")),
            "vega": as_float(r.get("vega")),
            "rho": as_float(r.get("rho")),

            "volume": as_int(r.get("volume")),
            "open_interest": as_int(r.get("openInterest") or r.get("open_interest") or r.get("oi")),

            "is_settlement": as_bool01(r.get("isSettlement") or r.get("is_settlement")),
        }
        upsert_mysql(engine, fact_opt, pk_cols=["option_id", "trade_date"], data=opt_row)

        wrote += 1
        print(f"OK: upserted option_id={oid} into dim_option_contract + fact_option_eod")
        time.sleep(sleep_s)

    return wrote


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True, help="Underlying symbol, e.g. AAPL")
    p.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")

    # Option slice config (defaults mimic your proven 4-row slice)
    p.add_argument("--dte", type=int, default=7, help="Target DTE for nearest-option-tickers (default: 7)")
    p.add_argument("--moneyness", default="0,5", help="Comma list of moneyness values (default: 0,5)")
    p.add_argument("--calls", action="store_true", default=True, help="Include calls (default: true)")
    p.add_argument("--no-calls", action="store_true", help="Disable calls")
    p.add_argument("--puts", action="store_true", default=True, help="Include puts (default: true)")
    p.add_argument("--no-puts", action="store_true", help="Disable puts")
    p.add_argument("--max-contracts", type=int, default=4, help="Max option contracts to load (default: 4)")
    p.add_argument("--sleep", type=float, default=0.25, help="Sleep seconds between API calls (default: 0.25)")

    args = p.parse_args()

    symbol = args.symbol.strip().upper()
    trade_date = parse_yyyy_mm_dd(args.date)

    include_calls = bool(args.calls) and not bool(args.no_calls)
    include_puts = bool(args.puts) and not bool(args.no_puts)

    moneyness_list: List[int] = []
    for part in str(args.moneyness).split(","):
        part = part.strip()
        if part:
            moneyness_list.append(int(part))

    cfg = load_config()
    client = IVolClient(cfg.base_url, cfg.api_key)

    os.environ["DB_URL"] = cfg.db_url
    engine = get_engine()

    md = MetaData()
    md.reflect(
        bind=engine,
        only=[
            "dim_underlying",
            "dim_option_contract",
            "fact_underlying_eod",
            "fact_option_eod",
            "fact_vol_metrics",
        ],
    )

    print(f"Base: {cfg.base_url}")
    print(f"Symbol: {symbol}  Date: {trade_date.isoformat()}")
    print(f"Slice: dte={args.dte} moneyness={moneyness_list} calls={include_calls} puts={include_puts} max={args.max_contracts}")

    underlying_id = ensure_underlying_id(engine, md, symbol)
    print(f"OK: underlying_id={underlying_id} for {symbol}")

    ok_eod = load_underlying_eod(client, engine, md, symbol, trade_date, underlying_id)
    print(f"EOD: {'OK' if ok_eod else 'SKIP'}")

    ok_vol = load_vol_metrics(client, engine, md, symbol, trade_date, underlying_id)
    print(f"VOL: {'OK' if ok_vol else 'SKIP'}")

    wrote_opts = load_options_for_date(
        client=client,
        engine=engine,
        md=md,
        symbol=symbol,
        trade_date=trade_date,
        underlying_id=underlying_id,
        dte=args.dte,
        moneyness_list=moneyness_list,
        include_calls=include_calls,
        include_puts=include_puts,
        max_contracts=args.max_contracts,
        sleep_s=args.sleep,
    )
    print(f"OPTIONS: wrote/upserted {wrote_opts} contracts")

    print("DONE ✅ daily snapshot load complete.")


if __name__ == "__main__":
    main()
