from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import MetaData

# Ensure imports work even if PYTHONPATH isn't set
THIS_FILE = Path(__file__).resolve()
SRC_DIR = THIS_FILE.parents[1]          # ...\src
PROJECT_ROOT = SRC_DIR.parents[0]       # project root
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Import your working daily loader functions
from ivol.load_daily_snapshot import (  # type: ignore
    IVolClient,
    ensure_underlying_id,
    load_config,
    load_options_for_date,
    load_underlying_eod,
    load_vol_metrics,
    parse_yyyy_mm_dd,
)
from db.engine import get_engine


@dataclass
class Progress:
    last_success_date: Optional[str] = None
    days_done: int = 0
    days_skipped: int = 0
    days_failed: int = 0
    last_error: Optional[str] = None


def daterange_inclusive(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def load_progress(path: Path) -> Progress:
    if not path.exists():
        return Progress()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        p = Progress()
        for k in p.__dict__.keys():
            setattr(p, k, data.get(k))
        return p
    except Exception:
        return Progress(last_error="Could not parse progress file; starting fresh.")


def save_progress(path: Path, p: Progress) -> None:
    path.write_text(json.dumps(p.__dict__, indent=2), encoding="utf-8")


def is_transient_error(msg: str) -> bool:
    m = msg.lower()
    return any(
        s in m
        for s in [
            "timeout",
            "timed out",
            "temporarily unavailable",
            "connection aborted",
            "connection reset",
            "connection refused",
            "429",
            "too many requests",
            "502",
            "503",
            "504",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
        ]
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")

    # slice config (defaults = your proven 4-contract slice)
    p.add_argument("--dte", type=int, default=7)
    p.add_argument("--moneyness", default="0,5")
    p.add_argument("--no-options", action="store_true", help="Only underlying + vol (skip options)")
    p.add_argument("--max-contracts", type=int, default=4)
    p.add_argument("--sleep", type=float, default=0.25, help="sleep between API calls inside a day")

    # backfill behavior
    p.add_argument("--day-sleep", type=float, default=0.10, help="sleep between days")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--max-errors", type=int, default=10)
    p.add_argument("--resume", action="store_true")

    args = p.parse_args()

    symbol = args.symbol.strip().upper()
    start = parse_yyyy_mm_dd(args.start)
    end = parse_yyyy_mm_dd(args.end)
    if end < start:
        raise SystemExit("--end must be >= --start")

    moneyness_list = [int(x.strip()) for x in str(args.moneyness).split(",") if x.strip()]
    do_options = not bool(args.no_options)

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

    progress_path = PROJECT_ROOT / f".backfill_progress_{symbol}.json"
    prog = load_progress(progress_path)

    skip_until: Optional[date] = None
    if args.resume and prog.last_success_date:
        skip_until = parse_yyyy_mm_dd(prog.last_success_date)
        print(f"RESUME: skipping dates <= {skip_until.isoformat()} (from {progress_path.name})")

    print(f"Base: {cfg.base_url}")
    print(f"Backfill: {symbol} {start.isoformat()} -> {end.isoformat()}")
    print(f"Slice: dte={args.dte} moneyness={moneyness_list} max={args.max_contracts} options={do_options}")
    print(f"Progress file: {progress_path}")

    for d in daterange_inclusive(start, end):
        if skip_until and d <= skip_until:
            continue

        print(f"\n=== {symbol} {d.isoformat()} ===")

        attempt = 0
        while True:
            try:
                underlying_id = ensure_underlying_id(engine, md, symbol)

                ok_eod = load_underlying_eod(client, engine, md, symbol, d, underlying_id)
                if not ok_eod:
                    prog.days_skipped += 1
                    prog.last_error = None
                    save_progress(progress_path, prog)
                    print("SKIP (no underlying EOD row - weekend/holiday likely)")
                    break

                ok_vol = load_vol_metrics(client, engine, md, symbol, d, underlying_id)
                vol_status = "VOL OK" if ok_vol else "VOL SKIP"

                opt_status = "OPTIONS SKIP"
                if do_options:
                    wrote = load_options_for_date(
                        client=client,
                        engine=engine,
                        md=md,
                        symbol=symbol,
                        trade_date=d,
                        underlying_id=underlying_id,
                        dte=args.dte,
                        moneyness_list=moneyness_list,
                        include_calls=True,
                        include_puts=True,
                        max_contracts=args.max_contracts,
                        sleep_s=args.sleep,
                    )
                    opt_status = f"OPTIONS wrote/upserted {wrote}"

                prog.days_done += 1
                prog.last_success_date = d.isoformat()
                prog.last_error = None
                save_progress(progress_path, prog)

                print(f"OK (EOD OK, {vol_status}, {opt_status})")
                break

            except Exception as e:
                msg = str(e)
                attempt += 1
                prog.days_failed += 1
                prog.last_error = msg
                save_progress(progress_path, prog)

                print(f"ERROR: {msg}")

                if prog.days_failed >= args.max_errors:
                    raise SystemExit(f"Stopped: hit --max-errors={args.max_errors}. See {progress_path.name}")

                if attempt > args.retries or not is_transient_error(msg):
                    print("FAIL (not retrying this day)")
                    break

                backoff = min(30.0, 2.0 ** (attempt - 1))
                print(f"RETRY in {backoff:.1f}s (attempt {attempt}/{args.retries})")
                time.sleep(backoff)

        time.sleep(args.day_sleep)

    print("\nDONE ✅ backfill complete")
    print(f"days_done={prog.days_done} days_skipped={prog.days_skipped} days_failed={prog.days_failed}")
    print(f"progress_file={progress_path}")


if __name__ == "__main__":
    main()
