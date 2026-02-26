"""Daily ingest — defaults to yesterday.  Designed for a scheduled task.

Examples:
  # Ingest yesterday for SPX (default)
  python scripts/daily_ingest.py --symbol SPX

  # Ingest a specific date for AAPL
  python scripts/daily_ingest.py --symbol AAPL --date 2026-02-24

  # Multiple symbols
  python scripts/daily_ingest.py --symbol SPX AAPL --date 2026-02-24
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date as dt_date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.db.engine import _load_env          # noqa: E402
from src.ivol.constants import BASE_URL_DEFAULT  # noqa: E402
from src.ivol.key_pool import load_key_pool   # noqa: E402
from src.backfill.runner import run_backfill   # noqa: E402

# Default parameters tuned for large-cap options (SPX-style)
DEFAULT_MAX_DTE     = 30
DEFAULT_STRIKE_LOW  = 0.90
DEFAULT_STRIKE_HIGH = 1.10
DEFAULT_PER_KEY_RPS = 0.3


def main() -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    yesterday = (dt_date.today() - timedelta(days=1)).isoformat()

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol",      nargs="+", default=["SPX"],
                    help="One or more symbols (default: SPX)")
    ap.add_argument("--date",        default=yesterday, metavar="YYYY-MM-DD",
                    help=f"Trade date (default: yesterday = {yesterday})")
    ap.add_argument("--region",      default="USA")
    ap.add_argument("--max-dte",     type=int,   default=DEFAULT_MAX_DTE,   dest="max_dte")
    ap.add_argument("--strike-low",  type=float, default=DEFAULT_STRIKE_LOW,  dest="strike_low")
    ap.add_argument("--strike-high", type=float, default=DEFAULT_STRIKE_HIGH, dest="strike_high")
    ap.add_argument("--max-workers", type=int,   default=0, dest="max_workers")
    ap.add_argument("--per-key-rps", type=float, default=DEFAULT_PER_KEY_RPS, dest="per_key_rps")
    ap.add_argument("--debug",       action="store_true")
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

    trade_date = dt_date.fromisoformat(args.date)

    for raw_sym in args.symbol:
        symbol = raw_sym.upper()
        progress_file = Path(f".daily_{symbol}_{args.date}.json")
        log_file = f"logs/daily_{symbol}_{ts}.log"
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)

        print(f"\n>>> Running daily ingest for {symbol} on {args.date}")
        run_backfill(
            symbol=symbol,
            start=trade_date,
            end=trade_date,
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
        )


if __name__ == "__main__":
    main()
