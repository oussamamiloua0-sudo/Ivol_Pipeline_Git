"""run_chain_for_date: orchestrates discovery + parallel raw-iv + DB write."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as dt_date
from typing import Optional

import requests
from sqlalchemy.engine import Engine

from src.db.engine import get_engine
from src.discovery.filters import filter_by_delta, filter_monthlies
from src.discovery.option_series import discover_contracts
from src.ingest.rawiv import fetch_rawiv
from src.ingest.schema_cache import ChainResult, SchemaCache, build_schema_cache
from src.ingest.writer import bulk_upsert, chunked, coerce_date, ensure_underlying
from src.ivol.constants import BASE_URL_DEFAULT, BULK_CHUNK_SIZE
from src.ivol.key_pool import KeyPool, load_key_pool


def run_chain_for_date(
    *,
    symbol: str,
    date: str,
    exp_from: str,
    exp_to: str,
    strike_from: float,
    strike_to: float,
    region: str               = "USA",
    base_url: str             = BASE_URL_DEFAULT,
    key_pool: KeyPool | None  = None,
    api_key: str              = "",
    per_key_rps: float        = 0.3,
    max_workers: int          = 0,
    debug: bool               = False,
    monthlies_only: bool             = False,
    spot_close: float | None         = None,
    delta_low: float | None          = None,
    delta_high: float | None         = None,
    delta_sigma: float               = 0.20,
    session: requests.Session | None = None,
    engine: Engine | None            = None,
    schema_cache: SchemaCache | None = None,
    known_option_ids: set[int] | None = None,
) -> ChainResult:
    """Full EOD chain ingest for one date.

    Steps:
      1. Discover all contracts (calls + puts) via option-series-on-date.
      2. Fetch raw-iv in parallel across all API keys (worker threads).
      3. DB writes in the main thread: dim_option_contract + fact_option_eod.

    Schema cache and known_option_ids are optional optimisations for the
    backfill runner (build once, reuse per date).
    """
    t0     = time.monotonic()
    result = ChainResult(date=date, symbol=symbol)
    log    = logging.getLogger("ivol.ingest")

    own_session = session is None
    own_engine  = engine is None

    try:
        if key_pool is None:
            key_pool = (
                KeyPool([api_key], per_key_rps=per_key_rps)
                if api_key
                else load_key_pool(per_key_rps=per_key_rps)
            )

        n_keys  = key_pool.count
        workers = max_workers if max_workers > 0 else min(n_keys, 8)

        if own_session:
            session = requests.Session()
        if own_engine:
            engine = get_engine()

        if schema_cache is None:
            schema_cache = build_schema_cache(engine)

        # ---- Step 1: Discover contracts ------------------------------------
        discovery_key = key_pool.acquire()
        shared = dict(
            session=session, base_url=base_url, api_key=discovery_key,
            symbol=symbol, date=date,
            exp_from=exp_from, exp_to=exp_to,
            strike_from=strike_from, strike_to=strike_to,
            region=region, debug=debug,
        )
        calls_rows = discover_contracts(**shared, call_put="C")
        puts_rows  = discover_contracts(**shared, call_put="P")

        result.discovered_calls = len(calls_rows)
        result.discovered_puts  = len(puts_rows)

        seen: set[int] = set()
        all_contracts: list[dict] = []
        for row in calls_rows + puts_rows:
            oid = row.get("optionId") or row.get("option_id") or row.get("id")
            if oid is None:
                continue
            oid = int(oid)
            if oid in seen:
                continue
            seen.add(oid)
            all_contracts.append(row)

        result.contracts_total = len(all_contracts)
        log.info(
            "[%s] Discovered %d contracts (calls=%d puts=%d)  keys=%d workers=%d",
            date, result.contracts_total,
            result.discovered_calls, result.discovered_puts,
            n_keys, workers,
        )

        # ---- Optional: monthlies-only filter --------------------------------
        if monthlies_only and all_contracts:
            all_contracts, dropped = filter_monthlies(all_contracts)
            result.contracts_total = len(all_contracts)
            log.info(
                "[%s] monthlies-only: kept=%d  dropped=%d (weeklies/other)",
                date, result.contracts_total, dropped,
            )

        # ---- Optional: delta filter (BS approximation, pre-rawiv) ----------
        if delta_low is not None and delta_high is not None and all_contracts:
            if spot_close is None or spot_close <= 0:
                log.warning(
                    "[%s] delta filter requested but spot_close not provided — skipping",
                    date,
                )
            else:
                from datetime import date as _dt_date
                trade_dt  = _dt_date.fromisoformat(date)
                all_contracts, d_dropped = filter_by_delta(
                    all_contracts, spot_close, trade_dt,
                    delta_low, delta_high, sigma=delta_sigma,
                )
                result.contracts_total = len(all_contracts)
                log.info(
                    "[%s] delta filter [%.3f, %.3f] sigma=%.2f: kept=%d  dropped=%d",
                    date, delta_low, delta_high, delta_sigma,
                    result.contracts_total, d_dropped,
                )

        if result.contracts_total == 0:
            result.elapsed_seconds = round(time.monotonic() - t0, 2)
            return result

        # ---- Step 2: Parallel raw-iv fetch ---------------------------------
        fetch_results: list[tuple[dict, dict | None, str | None]] = []
        t_fetch = time.monotonic()

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rawiv") as pool:
            future_map = {
                pool.submit(
                    fetch_rawiv,
                    key_pool=key_pool,
                    base_url=base_url,
                    option_id=int(c["optionId"]),
                    date=date,
                    region=region,
                ): c
                for c in all_contracts
            }
            for future in as_completed(future_map):
                contract = future_map[future]
                try:
                    raw, err = future.result()
                except Exception as exc:
                    raw, err = None, str(exc)
                fetch_results.append((contract, raw, err))

        fetch_elapsed = time.monotonic() - t_fetch
        result.avg_rps = round(len(all_contracts) / fetch_elapsed if fetch_elapsed > 0 else 0.0, 2)
        log.info(
            "[%s] Fetched %d raw-iv in %.1fs  avg=%.1f req/s",
            date, len(fetch_results), fetch_elapsed, result.avg_rps,
        )

        # ---- Step 3: DB writes (main thread only) --------------------------
        sc = schema_cache
        new_contract_rows: list[dict] = []
        new_contract_ids:  list[int]  = []
        fact_rows:         list[dict] = []

        for contract, raw, err in fetch_results:
            option_id  = int(contract["optionId"])
            exp_val    = coerce_date(contract.get("expirationDate"))
            strike_val = contract.get("strike")
            cp_val     = contract.get("callPut") or contract.get("Call/Put")
            osym_val   = (
                contract.get("OptionSymbol")
                or contract.get("optionSymbol")
                or contract.get("option_symbol")
                or contract.get("option symbol")
            )

            if raw is None:
                if err:
                    result.loaded_failed += 1
                    result.contract_errors.append({"option_id": option_id, "error": err})
                else:
                    result.skipped += 1
                continue

            if known_option_ids is None or option_id not in known_option_ids:
                crow = {k: v for k, v in {
                    sc.c_oid:    option_id,
                    sc.c_uid:    None,
                    sc.c_sym:    raw.get("symbol"),
                    sc.c_exch:   raw.get("exchange"),
                    sc.c_osym:   raw.get("option symbol") or osym_val,
                    sc.c_exp:    exp_val or coerce_date(raw.get("expiration")),
                    sc.c_strike: strike_val or raw.get("strike"),
                    sc.c_cp:     cp_val or raw.get("Call/Put"),
                    sc.c_style:  raw.get("style"),
                }.items() if k is not None}
                new_contract_rows.append(crow)
                new_contract_ids.append(option_id)
            else:
                result.contracts_skipped_known += 1

            frow = {k: v for k, v in {
                sc.f_oid:    option_id,
                sc.f_date:   dt_date.fromisoformat(date),
                sc.f_bid:    raw.get("bid"),
                sc.f_ask:    raw.get("ask"),
                sc.f_price:  raw.get("price"),
                sc.f_iv:     raw.get("iv"),
                sc.f_preiv:  raw.get("preiv"),
                sc.f_delta:  raw.get("delta"),
                sc.f_gamma:  raw.get("gamma"),
                sc.f_vega:   raw.get("vega"),
                sc.f_theta:  raw.get("theta"),
                sc.f_rho:    raw.get("rho"),
                sc.f_vol:    raw.get("volume"),
                sc.f_oi:     raw.get("open interest"),
                sc.f_settle: raw.get("is_settlement"),
            }.items() if k is not None}
            fact_rows.append(frow)

        result.contracts_new = len(new_contract_rows)

        with engine.begin() as conn:
            underlying_id = ensure_underlying(conn, sc, symbol)

        if new_contract_rows:
            if sc.c_uid:
                for crow in new_contract_rows:
                    crow[sc.c_uid] = underlying_id
            with engine.begin() as conn:
                bulk_upsert(conn, sc.tbl_c, new_contract_rows, conflict_cols=[sc.c_oid])
            if known_option_ids is not None:
                known_option_ids.update(new_contract_ids)
            log.debug("[%s] dim_option_contract: %d new rows", date, len(new_contract_rows))

        total_fact = 0
        for chunk_num, chunk in enumerate(chunked(fact_rows, BULK_CHUNK_SIZE), start=1):
            with engine.begin() as conn:
                n = bulk_upsert(conn, sc.tbl_f, chunk, conflict_cols=[sc.f_oid, sc.f_date])
            total_fact += n
            log.debug("[%s] fact_option_eod chunk %d: %d rows", date, chunk_num, n)

        result.loaded_ok = total_fact
        log.info(
            "[%s] DB done: fact=%d new_contracts=%d skipped_known=%d",
            date, result.loaded_ok, result.contracts_new, result.contracts_skipped_known,
        )

    except Exception as exc:
        result.success = False
        result.error   = str(exc)
        logging.getLogger("ivol.ingest").error("[%s] Fatal: %s", date, exc, exc_info=True)

    finally:
        if own_session and session is not None:
            session.close()
        result.elapsed_seconds = round(time.monotonic() - t0, 2)

    return result
