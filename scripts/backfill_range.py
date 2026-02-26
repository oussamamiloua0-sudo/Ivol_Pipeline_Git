"""Backfill a date range for one symbol.

Examples:
  # SPX — narrow strike band, short DTE (large index)
  python scripts/backfill_range.py --symbol SPX --start 2022-02-14 --end 2022-02-18 \
      --max-dte 30 --strike-low 0.90 --strike-high 1.10 --per-key-rps 0.3

  # AAPL — wide band, longer DTE
  python scripts/backfill_range.py --symbol AAPL --start 2022-01-03 --end 2022-12-31 \
      --max-dte 180 --strike-low 0.70 --strike-high 1.30 --per-key-rps 0.3

  # Retry only previously failed dates
  python scripts/backfill_range.py --symbol SPX --retry-failed
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date as dt_date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.db.engine import _load_env, get_engine   # noqa: E402
from src.ivol.constants import BASE_URL_DEFAULT   # noqa: E402
from src.ivol.key_pool import load_key_pool        # noqa: E402
from src.backfill.runner import run_backfill        # noqa: E402


def main() -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol",        default="SPX")
    ap.add_argument("--start",         default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--end",           default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--region",        default="USA")
    ap.add_argument("--max-dte",       type=int,   default=30,   dest="max_dte")
    ap.add_argument("--strike-low",    type=float, default=0.90, dest="strike_low")
    ap.add_argument("--strike-high",   type=float, default=1.10, dest="strike_high")
    ap.add_argument("--max-workers",   type=int,   default=0,    dest="max_workers")
    ap.add_argument("--per-key-rps",   type=float, default=0.3,  dest="per_key_rps",
                    help="RPS per API key. 0.3 = safe for 5-key setups (~1.5 rps total)")
    ap.add_argument("--monthlies-only",    action="store_true", dest="monthlies_only",
                    help="Keep only contracts expiring on the 3rd Friday (or next Sat) of the month")
    ap.add_argument("--trading-days-only", action="store_true", dest="trading_days_only",
                    help="Use NYSE calendar to skip weekends and holidays (no wasted spot-price calls)")
    ap.add_argument("--delta-low",  type=float, default=None, dest="delta_low",
                    metavar="DELTA",
                    help="Lower bound for BS-approximate delta filter (e.g. -0.10). "
                         "If omitted, no delta filter is applied.")
    ap.add_argument("--delta-high", type=float, default=None, dest="delta_high",
                    metavar="DELTA",
                    help="Upper bound for BS-approximate delta filter (e.g. 0.10). "
                         "Both --delta-low and --delta-high must be set to activate the filter.")
    ap.add_argument("--delta-sigma", type=float, default=0.20, dest="delta_sigma",
                    metavar="SIGMA",
                    help="Annualized vol assumption for BS delta approximation (default: 0.20)")
    ap.add_argument("--retry-failed",  action="store_true", dest="retry_failed")
    ap.add_argument("--progress-file", default=None, dest="progress_file")
    ap.add_argument("--log-file",      default=None, dest="log_file")
    ap.add_argument("--debug",         action="store_true")
    args = ap.parse_args()

    _load_env()

    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL") or ""
    if not db_url:
        raise SystemExit("ERROR: DB_URL not set in .env")
    os.environ["DB_URL"] = db_url.strip().strip('"').strip("'")

    base_url = (os.getenv("IVOL_BASE_URL") or BASE_URL_DEFAULT).rstrip("/")

    try:
        key_pool = load_key_pool(per_key_rps=args.per_key_rps)
    except RuntimeError as exc:
        raise SystemExit(f"ERROR: {exc}")

    symbol = args.symbol.upper()

    if not args.retry_failed:
        if not args.start or not args.end:
            raise SystemExit("ERROR: --start and --end are required (or use --retry-failed)")
        start = dt_date.fromisoformat(args.start)
        end   = dt_date.fromisoformat(args.end)
        if start > end:
            raise SystemExit(f"ERROR: --start {start} is after --end {end}")
    else:
        start = dt_date.fromisoformat(args.start) if args.start else None
        end   = dt_date.fromisoformat(args.end)   if args.end   else None

    if args.progress_file:
        progress_file = Path(args.progress_file)
    elif args.start and args.end:
        progress_file = Path(f".backfill_{symbol}_{args.start}_{args.end}.json")
    else:
        candidates = sorted(Path(".").glob(f".backfill_{symbol}_*.json"))
        if not candidates:
            raise SystemExit(f"ERROR: no .backfill_{symbol}_*.json checkpoint found.")
        progress_file = candidates[-1]

    log_file = args.log_file or f"logs/backfill_{symbol}_{ts}.log"
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    run_backfill(
        symbol=symbol,
        start=start,
        end=end,
        region=args.region,
        max_dte=args.max_dte,
        strike_low=args.strike_low,
        strike_high=args.strike_high,
        progress_file=progress_file,
        log_file=log_file,
        debug=args.debug,
        key_pool=key_pool,
        base_url=base_url,
        max_workers=args.max_workers,
        per_key_rps=args.per_key_rps,
        retry_failed=args.retry_failed,
        monthlies_only=args.monthlies_only,
        trading_days_only=args.trading_days_only,
        delta_low=args.delta_low,
        delta_high=args.delta_high,
        delta_sigma=args.delta_sigma,
    )


if __name__ == "__main__":
    main()
