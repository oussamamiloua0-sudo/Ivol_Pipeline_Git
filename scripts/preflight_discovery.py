"""Discovery preflight: contract counts + DB state + workload summary.

Runs discovery (option-series API) for each trading date in a range,
applies optional post-filters (monthlies, delta), counts existing DB rows,
and prints a per-stage workload summary.  Does NOT call the rawiv endpoint.

Filter pipeline shown in output:
  discovered (API: max_dte + strike applied server-side)
    → monthlies filter  (if --monthlies-only)
    → delta filter      (if --delta-low / --delta-high provided)
    = rawiv_calls       (contracts that will hit the rawiv endpoint)

Examples:
  # SPY last full NYSE week, monthlies only
  python scripts/preflight_discovery.py --symbol SPY --last-week --monthlies-only

  # Explicit date range
  python scripts/preflight_discovery.py --symbol SPY --start 2026-02-09 --end 2026-02-13 --monthlies-only

  # With delta filter (deep OTM only: |delta| <= 0.10)
  python scripts/preflight_discovery.py --symbol SPY --start 2026-02-09 --end 2026-02-13 \\
      --monthlies-only --delta-low -0.10 --delta-high 0.10

  # No filter (all expirations)
  python scripts/preflight_discovery.py --symbol SPY --start 2026-02-09 --end 2026-02-13
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date as dt_date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import requests
import pandas_market_calendars as mcal
from sqlalchemy import text

from src.db.engine import _load_env, get_engine        # noqa: E402
from src.ivol.constants import BASE_URL_DEFAULT        # noqa: E402
from src.ivol.key_pool import load_key_pool            # noqa: E402
from src.backfill.runner import _fetch_spot_close      # noqa: E402
from src.discovery.option_series import discover_contracts  # noqa: E402
from src.discovery.filters import filter_monthlies, filter_by_delta     # noqa: E402

import logging
logging.basicConfig(level=logging.WARNING)   # suppress info noise during preflight
_log = logging.getLogger("preflight")


# ---------------------------------------------------------------------------
# NYSE calendar helpers
# ---------------------------------------------------------------------------

def _nyse_trading_days(start: dt_date, end: dt_date) -> list[dt_date]:
    nyse = mcal.get_calendar("NYSE")
    sched = nyse.schedule(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )
    return [d.date() for d in sched.index]


def _last_full_trading_week(today: dt_date) -> tuple[dt_date, dt_date]:
    """Return (monday, friday) of the most recent completed NYSE week with 5 trading days.

    "Full" means all 5 Mon-Fri slots are NYSE trading days (no holidays).
    Walks back week by week until a full 5-day week is found.
    """
    nyse = mcal.get_calendar("NYSE")
    # Start from the Monday of the calendar week *before* the current one
    # (current week may be incomplete)
    monday = today - timedelta(days=today.weekday())   # this week's Monday
    monday -= timedelta(weeks=1)                        # go back one week

    for _ in range(52):   # guard: look back at most a year
        friday = monday + timedelta(days=4)
        sched = nyse.schedule(
            start_date=monday.isoformat(),
            end_date=friday.isoformat(),
        )
        if len(sched) == 5:
            return monday, friday
        monday -= timedelta(weeks=1)

    raise RuntimeError("Could not find a full 5-day NYSE trading week in the past year")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db_counts_by_date(engine, symbol: str, dates: list[dt_date]) -> dict[str, int]:
    """Return {date_str: row_count} for fact_option_eod filtered by symbol."""
    if not dates:
        return {}
    date_list = ", ".join(f"'{d.isoformat()}'" for d in dates)
    sql = f"""
        SELECT f.trade_date, COUNT(*) AS cnt
        FROM   fact_option_eod f
        JOIN   dim_option_contract c ON c.option_id     = f.option_id
        JOIN   dim_underlying      u ON u.underlying_id = c.underlying_id
        WHERE  u.symbol    = :symbol
          AND  f.trade_date IN ({date_list})
        GROUP BY f.trade_date
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"symbol": symbol}).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}
    except Exception as exc:
        _log.warning("DB count query failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--symbol",         default="SPY",  help="Underlying symbol (default: SPY)")
    ap.add_argument("--start",          default=None,   metavar="YYYY-MM-DD")
    ap.add_argument("--end",            default=None,   metavar="YYYY-MM-DD")
    ap.add_argument("--last-week",      action="store_true", dest="last_week",
                    help="Auto-compute the previous full NYSE trading week")
    ap.add_argument("--region",         default="USA")
    ap.add_argument("--max-dte",        type=int,   default=60,  dest="max_dte")
    ap.add_argument("--strike-low",     type=float, default=0.80, dest="strike_low")
    ap.add_argument("--strike-high",    type=float, default=1.20, dest="strike_high")
    ap.add_argument("--monthlies-only", action="store_true", dest="monthlies_only")
    ap.add_argument("--per-key-rps",    type=float, default=0.3, dest="per_key_rps")
    # Delta filter (optional, additive — applied AFTER strike + monthlies)
    ap.add_argument("--delta-low",  type=float, default=None, dest="delta_low",
                    metavar="DELTA",
                    help="Lower bound for BS-approx delta filter (e.g. -0.10). "
                         "Requires --delta-high. Does NOT replace strike filter.")
    ap.add_argument("--delta-high", type=float, default=None, dest="delta_high",
                    metavar="DELTA",
                    help="Upper bound for BS-approx delta filter (e.g. 0.10).")
    ap.add_argument("--delta-sigma", type=float, default=0.20, dest="delta_sigma",
                    metavar="SIGMA",
                    help="Annualized vol assumption for BS delta calc (default: 0.20)")
    args = ap.parse_args()

    _load_env()
    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL") or ""
    if not db_url:
        raise SystemExit("ERROR: DB_URL not set in .env")
    os.environ["DB_URL"] = db_url.strip().strip('"').strip("'")

    base_url = (os.getenv("IVOL_BASE_URL") or BASE_URL_DEFAULT).rstrip("/")
    symbol   = args.symbol.upper()

    # Validate delta flags
    use_delta = args.delta_low is not None and args.delta_high is not None
    if (args.delta_low is None) != (args.delta_high is None):
        raise SystemExit("ERROR: --delta-low and --delta-high must both be set (or both omitted)")

    # ---- Resolve date range -----------------------------------------------
    today = dt_date.today()
    if args.last_week:
        start_date, end_date = _last_full_trading_week(today)
    elif args.start and args.end:
        start_date = dt_date.fromisoformat(args.start)
        end_date   = dt_date.fromisoformat(args.end)
    else:
        raise SystemExit("ERROR: provide --start/--end or use --last-week")

    trading_days = _nyse_trading_days(start_date, end_date)

    line = "=" * 72

    print(line)
    print(f"  PREFLIGHT DISCOVERY  symbol={symbol}")
    print(f"  today={today}   range={start_date} to {end_date}")
    if args.last_week:
        print(f"  (auto: last full NYSE trading week)")
    print(f"  monthlies-only={args.monthlies_only}   max_dte={args.max_dte}")
    print(f"  strike_band={args.strike_low}-{args.strike_high}   per_key_rps={args.per_key_rps}")
    if use_delta:
        print(f"  delta_filter=[{args.delta_low}, {args.delta_high}]  sigma={args.delta_sigma}  "
              f"(BS approx, additive after strike+monthlies)")
    else:
        print(f"  delta_filter=disabled")
    print(line)

    # ---- C) Print trading dates -------------------------------------------
    print(f"\nC) NYSE trading dates in range ({len(trading_days)} days):")
    for d in trading_days:
        print(f"     {d}")

    # ---- Setup API + DB ----------------------------------------------------
    try:
        key_pool = load_key_pool(per_key_rps=args.per_key_rps)
    except RuntimeError as exc:
        raise SystemExit(f"ERROR: {exc}")

    engine  = get_engine()
    session = requests.Session()

    # ---- D1) Discovery per trade date ------------------------------------
    # Column widths depend on which filters are active
    print(f"\nD1) Filter pipeline per trade date (API: max_dte={args.max_dte}, "
          f"strike={args.strike_low:.0%}-{args.strike_high:.0%} applied server-side):")

    if use_delta:
        hdr = (f"    {'Trade date':<12}  {'raw':>6}  {'monthly':>8}  "
               f"{'delta':>7}  {'=rawiv':>7}  Expiry breakdown")
        sep = f"    {'-'*12}  {'-'*6}  {'-'*8}  {'-'*7}  {'-'*7}  {'-'*30}"
    else:
        hdr = (f"    {'Trade date':<12}  {'raw':>6}  {'monthly':>8}  "
               f"{'=rawiv':>7}  Expiry breakdown")
        sep = f"    {'-'*12}  {'-'*6}  {'-'*8}  {'-'*7}  {'-'*30}"
    print(hdr)
    print(sep)

    per_date: dict[str, dict] = {}   # date_str -> stage counts + exp_breakdown

    for trade_date in trading_days:
        date_str  = trade_date.isoformat()
        exp_to    = trade_date + timedelta(days=args.max_dte)

        spot = _fetch_spot_close(session, base_url, key_pool, symbol,
                                 date_str, args.region, _log)
        if spot is None:
            print(f"    {date_str:<12}  (not a trading day — skipped)")
            continue

        sfrom = round(spot * args.strike_low,  2)
        sto   = round(spot * args.strike_high, 2)

        calls = discover_contracts(session, key_pool.acquire(), symbol, trade_date,
                                   trade_date, exp_to, sfrom, sto,
                                   "C", args.region, base_url=base_url)
        puts  = discover_contracts(session, key_pool.acquire(), symbol, trade_date,
                                   trade_date, exp_to, sfrom, sto,
                                   "P", args.region, base_url=base_url)

        raw_total     = len(calls) + len(puts)
        all_contracts = calls + puts

        # Stage 1 → 2: monthlies filter
        if args.monthlies_only:
            after_monthlies, _ = filter_monthlies(all_contracts)
        else:
            after_monthlies = all_contracts
        n_monthlies = len(after_monthlies)

        # Stage 2 → 3: delta filter (BS approximation)
        if use_delta and spot > 0:
            after_delta, _ = filter_by_delta(
                after_monthlies, spot, trade_date,
                args.delta_low, args.delta_high, sigma=args.delta_sigma,
            )
        else:
            after_delta = after_monthlies
        n_delta  = len(after_delta)
        n_rawiv  = n_delta   # final count going to rawiv

        # Expiry breakdown of final (rawiv) set
        exp_counts: dict[str, int] = defaultdict(int)
        for c in after_delta:
            exp = (c.get("expirationDate") or c.get("expiration_date") or "?")[:10]
            exp_counts[exp] += 1
        exp_summary = "  ".join(f"{e}({n})" for e, n in sorted(exp_counts.items()))

        per_date[date_str] = {
            "raw":         raw_total,
            "monthlies":   n_monthlies,
            "delta":       n_delta,
            "rawiv":       n_rawiv,
            "spot":        spot,
            "exp_counts":  dict(exp_counts),
        }

        if use_delta:
            print(f"    {date_str:<12}  {raw_total:>6,}  {n_monthlies:>8,}  "
                  f"{n_delta:>7,}  {n_rawiv:>7,}  {exp_summary}")
        else:
            print(f"    {date_str:<12}  {raw_total:>6,}  {n_monthlies:>8,}  "
                  f"{n_rawiv:>7,}  {exp_summary}")

    # ---- D2) DB counts for these dates -----------------------------------
    print(f"\nD2) Existing DB rows (fact_option_eod, symbol={symbol}):")
    print(f"    {'Trade date':<12}  {'DB rows':>10}  Status")
    print(f"    {'-'*40}")

    db_counts = _db_counts_by_date(engine, symbol, trading_days)
    for trade_date in trading_days:
        date_str = trade_date.isoformat()
        cnt      = db_counts.get(date_str, 0)
        status   = "already loaded" if cnt > 0 else "EMPTY (fresh)"
        print(f"    {date_str:<12}  {cnt:>10,}  {status}")

    # ---- D3) Workload summary --------------------------------------------
    total_rawiv_calls = sum(v["rawiv"] for v in per_date.values())
    num_keys          = key_pool.count

    print(f"\nD3) Workload summary:")
    print(f"    {'Stage':<30}  {'Contracts':>12}")
    print(f"    {'-'*46}")
    total_raw       = sum(v["raw"]       for v in per_date.values())
    total_monthlies = sum(v["monthlies"] for v in per_date.values())
    total_delta     = sum(v["delta"]     for v in per_date.values())
    print(f"    {'discovered (via API params)':<30}  {total_raw:>12,}")
    if args.monthlies_only:
        print(f"    {'after monthlies filter':<30}  {total_monthlies:>12,}  "
              f"(dropped {total_raw - total_monthlies:,})")
    if use_delta:
        prev = total_monthlies if args.monthlies_only else total_raw
        print(f"    {'after delta filter (BS approx)':<30}  {total_delta:>12,}  "
              f"(dropped {prev - total_delta:,})")
    print(f"    {'= rawiv calls needed':<30}  {total_rawiv_calls:>12,}")
    print()
    print(f"    Number of API keys                                : {num_keys}")
    print(f"    Per-key RPS                                       : {args.per_key_rps}")
    print()
    print(f"    Lower-bound time formula:")
    print(f"      time_seconds = rawiv_calls / (num_keys * per_key_rps)")
    print(f"      time_seconds = {total_rawiv_calls} / ({num_keys} * {args.per_key_rps})")
    print(f"      time_seconds = {total_rawiv_calls} / {num_keys * args.per_key_rps:.2f}")
    print(f"      (Does not include 429 back-off or DB write time)")

    # ---- D4) Exact PowerShell commands -----------------------------------
    mo_flag = " `\n    --monthlies-only" if args.monthlies_only else ""
    dl_flag = (f" `\n    --delta-low {args.delta_low} --delta-high {args.delta_high} "
               f"--delta-sigma {args.delta_sigma}") if use_delta else ""

    pf_cmd = (
        f".venv\\Scripts\\python.exe scripts/preflight_discovery.py "
        f"--symbol {symbol} --start {start_date} --end {end_date}"
        f"{' --monthlies-only' if args.monthlies_only else ''}"
        f"{f' --delta-low {args.delta_low} --delta-high {args.delta_high} --delta-sigma {args.delta_sigma}' if use_delta else ''} "
        f"--max-dte {args.max_dte} --strike-low {args.strike_low} --strike-high {args.strike_high} "
        f"--per-key-rps {args.per_key_rps}"
    )
    bf_cmd = (
        f".venv\\Scripts\\python.exe scripts/backfill_range.py `\n"
        f"    --symbol {symbol} --start {start_date} --end {end_date} `\n"
        f"    --max-dte {args.max_dte} --strike-low {args.strike_low} "
        f"--strike-high {args.strike_high} `\n"
        f"    --per-key-rps {args.per_key_rps}{mo_flag}{dl_flag}"
    )

    print(f"\nD4) Exact PowerShell commands:")
    print(f"\n  # Preflight (re-run any time; safe, no writes):")
    print(f"  {pf_cmd}")
    print(f"\n  # Backfill (DO NOT run until you type GO):")
    print(f"  $ts  = Get-Date -Format 'yyyyMMdd_HHmmss'")
    print(f"  $log = \"logs\\backfill_{symbol}_{start_date}_{end_date}_${{ts}}.log\"")
    print(f"  {bf_cmd} `\n    2>&1 | Tee-Object -FilePath $log")
    print(f"\n  # Tail log in a second terminal:")
    print(f"  Get-Content $log -Wait")

    print(f"\n{line}")
    print(f"  PREFLIGHT COMPLETE — waiting for GO")
    print(f"  Type exactly:  GO")
    print(f"  to start the backfill.")
    print(line)

    session.close()


if __name__ == "__main__":
    main()
