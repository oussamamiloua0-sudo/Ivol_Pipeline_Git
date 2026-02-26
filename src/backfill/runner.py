"""Backfill runner: date loop, spot-price check, circuit breaker, checkpoint."""
from __future__ import annotations

import logging
import sys
import time
from datetime import date as dt_date, timedelta
from pathlib import Path
from typing import Optional

import requests

from src.backfill.checkpoint import load_progress, save_progress
from src.db.engine import get_engine
from src.ingest.runner import run_chain_for_date
from src.ingest.schema_cache import build_schema_cache
from src.ivol.constants import BASE_URL_DEFAULT, RETRY_DELAYS, RETRY_STATUSES, STOCK_PRICES_PATH
from src.ivol.key_pool import KeyPool

CIRCUIT_BREAKER_LIMIT = 5


# ---------------------------------------------------------------------------
# Spot price
# ---------------------------------------------------------------------------

def _fetch_spot_close(
    session: requests.Session,
    base_url: str,
    key_pool: KeyPool,
    symbol: str,
    date: str,
    region: str,
    logger: logging.Logger,
) -> Optional[float]:
    """Return spot close for date, or None if not a trading day."""
    api_key = key_pool.acquire()
    params  = {"apiKey": api_key, "symbol": symbol, "date": date, "region": region}
    url     = f"{base_url}{STOCK_PRICES_PATH}"
    last_exc: Optional[Exception] = None

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 429:
                key_pool.record_429()
                logger.warning("HTTP 429 (attempt %d) — will retry", attempt)
                continue
            if r.status_code in RETRY_STATUSES - {429}:
                logger.warning("HTTP %s (attempt %d) — will retry", r.status_code, attempt)
                continue
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            logger.warning("spot-price request error attempt %d: %s", attempt, exc)
            last_exc = exc

    if last_exc:
        logger.warning("[%s] spot-price request failed: %s", date, last_exc)
        return None

    if r.status_code != 200:
        logger.debug("[%s] spot-price HTTP %s — skipping", date, r.status_code)
        return None

    try:
        body = r.json()
    except ValueError:
        logger.warning("[%s] spot-price not JSON", date)
        return None

    data = body.get("data") or []
    if not data:
        logger.debug("[%s] spot-price 0 rows — not a trading day", date)
        return None

    row   = data[0]
    close = row.get("close") or row.get("Close") or row.get("adjClose") or row.get("adj_close")
    if close is None:
        logger.warning("[%s] spot-price row has no 'close': %s", date, row)
        return None

    try:
        val = float(close)
    except (TypeError, ValueError):
        logger.warning("[%s] spot 'close' not float: %r", date, close)
        return None

    if val <= 0:
        return None

    key_pool.record_success()
    return val


# ---------------------------------------------------------------------------
# Known option IDs
# ---------------------------------------------------------------------------

def _load_known_option_ids(engine) -> set[int]:
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT option_id FROM dim_option_contract")).fetchall()
    ids = {int(r[0]) for r in rows}
    return ids


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(log_file: Optional[str], debug: bool) -> logging.Logger:
    level   = logging.DEBUG if debug else logging.INFO
    fmt     = "%(asctime)s %(levelname)-8s %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        lp = Path(log_file)
        lp.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(lp, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)
    logger = logging.getLogger("ivol.backfill")
    logger.setLevel(level)
    return logger


# ---------------------------------------------------------------------------
# Date iterators
# ---------------------------------------------------------------------------

def _date_range(start: dt_date, end: dt_date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _nyse_trading_days(start: dt_date, end: dt_date) -> list[dt_date]:
    """Return NYSE trading days in [start, end] using pandas_market_calendars."""
    import pandas_market_calendars as mcal
    nyse  = mcal.get_calendar("NYSE")
    sched = nyse.schedule(start_date=start.isoformat(), end_date=end.isoformat())
    return [d.date() for d in sched.index]


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------

def run_backfill(
    symbol: str,
    start: Optional[dt_date],
    end: Optional[dt_date],
    region: str,
    max_dte: int,
    strike_low: float,
    strike_high: float,
    progress_file: Path,
    log_file: Optional[str],
    debug: bool,
    key_pool: KeyPool,
    base_url: str = BASE_URL_DEFAULT,
    max_workers: int = 0,
    per_key_rps: float = 0.3,
    retry_failed: bool = False,
    monthlies_only: bool = False,
    trading_days_only: bool = False,
    delta_low: Optional[float] = None,
    delta_high: Optional[float] = None,
    delta_sigma: float = 0.20,
) -> None:
    logger = _setup_logging(log_file, debug)
    logger.info("=" * 72)
    if retry_failed:
        logger.info("Backfill RETRY-FAILED  symbol=%s", symbol)
    else:
        logger.info("Backfill start  symbol=%s  %s to %s", symbol, start, end)
    logger.info(
        "max_dte=%d  strike_band=%.2f-%.2f  keys=%d  workers=%d  rps/key=%.1f",
        max_dte, strike_low, strike_high,
        key_pool.count, max_workers or key_pool.count, per_key_rps,
    )
    if monthlies_only:
        logger.info("monthlies-only filter ENABLED (3rd Friday rule)")
    if trading_days_only:
        logger.info("trading-days-only ENABLED (NYSE calendar; weekends+holidays skipped)")
    if delta_low is not None and delta_high is not None:
        logger.info(
            "delta filter ENABLED  [%.3f, %.3f]  BS sigma=%.2f",
            delta_low, delta_high, delta_sigma,
        )
    logger.info("Progress file: %s", progress_file)
    logger.info("Keys: %s", ", ".join(key_pool.all_redacted()))
    logger.info("=" * 72)

    state     = load_progress(progress_file)
    last_done = state.get("last_completed_date")
    if last_done and not retry_failed:
        logger.info("Resuming from after %s  (done=%s failed=%s)",
                    last_done, state["days_done"], state["days_failed"])

    engine = get_engine()

    logger.info("A1: reflecting schema cache...")
    try:
        schema_cache = build_schema_cache(engine)
        logger.info("A1: schema cache ready.")
    except RuntimeError as exc:
        raise SystemExit(f"ERROR (schema cache): {exc}")

    logger.info("A2: loading known option IDs...")
    known_option_ids = _load_known_option_ids(engine)
    logger.info("A2: loaded %d known option_ids", len(known_option_ids))

    session = requests.Session()

    total_dates = total_done = total_skipped = total_failed = 0
    grand_contracts = grand_loaded = grand_errors = 0
    consecutive_failures = 0

    if retry_failed:
        failed_dates = sorted(state.get("failures", {}).keys())
        if not failed_dates:
            logger.info("No failed dates in checkpoint — nothing to retry.")
            return
        logger.info("Retrying %d failed date(s): %s", len(failed_dates), failed_dates)
        date_iterator = (dt_date.fromisoformat(d) for d in failed_dates)
    elif trading_days_only:
        trading_days = _nyse_trading_days(start, end)
        logger.info("NYSE calendar: %d trading days in range", len(trading_days))
        date_iterator = iter(trading_days)
    else:
        date_iterator = _date_range(start, end)

    try:
        for trade_date in date_iterator:
            date_str = trade_date.isoformat()

            if not retry_failed and last_done and date_str <= last_done:
                continue

            total_dates += 1
            t_day = time.monotonic()

            spot_close = _fetch_spot_close(
                session, base_url, key_pool, symbol, date_str, region, logger
            )
            if spot_close is None:
                logger.info("[%s] SKIP (not a trading day)", date_str)
                total_skipped += 1
                state["days_skipped"] = state.get("days_skipped", 0) + 1
                save_progress(progress_file, state)
                continue

            logger.info("[%s] spot_close=%.4f", date_str, spot_close)

            exp_from_date = trade_date
            exp_to_date   = trade_date + timedelta(days=max_dte)
            strike_from   = round(spot_close * strike_low,  2)
            strike_to     = round(spot_close * strike_high, 2)

            try:
                result = run_chain_for_date(
                    symbol=symbol,
                    date=date_str,
                    exp_from=exp_from_date.isoformat(),
                    exp_to=exp_to_date.isoformat(),
                    strike_from=strike_from,
                    strike_to=strike_to,
                    region=region,
                    base_url=base_url,
                    key_pool=key_pool,
                    per_key_rps=per_key_rps,
                    max_workers=max_workers,
                    debug=debug,
                    monthlies_only=monthlies_only,
                    spot_close=spot_close,
                    delta_low=delta_low,
                    delta_high=delta_high,
                    delta_sigma=delta_sigma,
                    session=session,
                    engine=engine,
                    schema_cache=schema_cache,
                    known_option_ids=known_option_ids,
                )
            except Exception as exc:
                logger.error("[%s] FATAL: %s", date_str, exc, exc_info=debug)
                total_failed += 1
                state["days_failed"] += 1
                state["failures"][date_str] = str(exc)
                save_progress(progress_file, state)
                consecutive_failures += 1
                if consecutive_failures >= CIRCUIT_BREAKER_LIMIT:
                    logger.critical("CIRCUIT BREAKER: %d consecutive failures — halting.", consecutive_failures)
                    break
                continue

            elapsed = time.monotonic() - t_day

            # A confirmed trading day that yields 0 contracts = discovery failure.
            # Mark as failed so --retry-failed will re-attempt it.
            if result.success and result.contracts_total == 0:
                result.success = False
                result.error   = "0 contracts discovered on confirmed trading day"
                logger.warning(
                    "[%s] ZERO-CONTRACTS spot_close=%.4f — recording as failed",
                    date_str, spot_close,
                )

            if result.success:
                logger.info(
                    "[%s] OK  calls=%d puts=%d contracts=%d "
                    "loaded=%d failed=%d skipped=%d  "
                    "new_dim=%d skipped_known=%d  %.1fs  rps=%.1f",
                    date_str,
                    result.discovered_calls, result.discovered_puts,
                    result.contracts_total,
                    result.loaded_ok, result.loaded_failed, result.skipped,
                    result.contracts_new, result.contracts_skipped_known,
                    elapsed, result.avg_rps,
                )
                total_done      += 1
                grand_contracts += result.contracts_total
                grand_loaded    += result.loaded_ok
                grand_errors    += result.loaded_failed
                if not retry_failed:
                    state["last_completed_date"] = date_str
                state["days_done"] += 1
                state["failures"].pop(date_str, None)
                consecutive_failures = 0

            else:
                logger.warning(
                    "[%s] FAIL  error=%s  contracts=%d loaded=%d  %.1fs",
                    date_str, result.error,
                    result.contracts_total, result.loaded_ok, elapsed,
                )
                total_failed += 1
                state["days_failed"] += 1
                state["failures"][date_str] = result.error or "unknown"
                grand_contracts += result.contracts_total
                grand_loaded    += result.loaded_ok
                grand_errors    += result.loaded_failed
                consecutive_failures += 1
                if consecutive_failures >= CIRCUIT_BREAKER_LIMIT:
                    logger.critical("CIRCUIT BREAKER: %d consecutive failures — halting.", consecutive_failures)
                    save_progress(progress_file, state)
                    break

            save_progress(progress_file, state)

    finally:
        session.close()

    logger.info("=" * 72)
    logger.info("BACKFILL COMPLETE")
    logger.info("  symbol       : %s", symbol)
    if retry_failed:
        logger.info("  mode         : retry-failed")
    else:
        logger.info("  date range   : %s to %s", start, end)
    logger.info("  keys used    : %d  [%s]", key_pool.count, ", ".join(key_pool.all_redacted()))
    logger.info("  dates seen   : %d", total_dates)
    logger.info("  days OK      : %d", total_done)
    logger.info("  days skipped : %d  (weekends/holidays)", total_skipped)
    logger.info("  days failed  : %d", total_failed)
    logger.info("  contracts    : %d total", grand_contracts)
    logger.info("  loaded OK    : %d  (fact_option_eod rows)", grand_loaded)
    logger.info("  load errors  : %d  (contract-level)", grand_errors)
    logger.info("  known IDs    : %d  (after run)", len(known_option_ids))
    logger.info("=" * 72)

    if state["failures"]:
        logger.warning("Failed dates still in checkpoint:")
        for d, err in sorted(state["failures"].items()):
            logger.warning("  %s  ->  %s", d, err)
