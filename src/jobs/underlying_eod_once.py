from __future__ import annotations

import argparse

from config import Settings
from db.engine import get_engine
from db.underlying_loader import get_or_create_underlying_id, upsert_underlying_eod
from ivol.client import IvolClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()

    s = Settings()
    engine = get_engine()
    client = IvolClient(s)

    token = client.get_token()
    res = client.stock_prices(token, args.symbol, args.date, args.date)

    print("status:", res.get("status"))
    data = res.get("data", [])
    if not data:
        raise SystemExit(f"No data returned for {args.symbol} {args.date}")

    row = data[0]

    underlying_id = get_or_create_underlying_id(engine, args.symbol)
    upsert_underlying_eod(engine, underlying_id, row)

    print(f"Loaded underlying EOD: symbol={args.symbol} date={args.date} underlying_id={underlying_id}")


if __name__ == "__main__":
    main()
