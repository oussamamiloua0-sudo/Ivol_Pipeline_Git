"""iVolatility option-series discovery.

Handles both sync (small result sets, data[] inline) and async delivery
(large result sets: data[] empty + status.urlForDetails → urlForDownload → gzip CSV).
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
import time

import requests

from src.ivol.constants import (
    ASYNC_INFO_POLL_DELAYS,
    BASE_URL_DEFAULT,
    DISCOVERY_PATH,
    RETRY_DELAYS,
    RETRY_STATUSES,
)


def _fetch_async_discovery(
    session: requests.Session,
    url_for_details: str,
    call_put: str,
    log: logging.Logger,
    timeout: int = 60,
) -> list[dict]:
    """
    Follow the iVol async delivery chain (3-hop) for large result sets.

    Hop 2 — GET urlForDetails (no apiKey):
        Returns JSON list:
        [ { "meta": { "status": "COMPLETE", "recordsCount": N,
                      "columns": "OptionSymbol,callPut,strike,expirationDate,optionId" },
            "data": [ { "urlForDownload": "https://.../data/download/<uuid>" } ] } ]
        Polls ASYNC_INFO_POLL_DELAYS until meta.status == "COMPLETE".

    Hop 3 — GET urlForDownload (no apiKey):
        application/gzip of CSV with header:
            OptionSymbol,callPut,strike,expirationDate,optionId

    Returns list[dict] with those CSV column names as keys.
    """
    url_for_download: str | None = None

    for i, delay in enumerate(ASYNC_INFO_POLL_DELAYS):
        if delay:
            time.sleep(delay)

        try:
            r2 = session.get(url_for_details, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            log.warning("  [async %s] urlForDetails attempt %d failed: %s", call_put, i + 1, exc)
            continue

        if r2.status_code != 200:
            log.warning("  [async %s] urlForDetails HTTP %s (attempt %d)", call_put, r2.status_code, i + 1)
            continue

        try:
            info_list = r2.json()
        except ValueError:
            log.warning("  [async %s] urlForDetails not JSON (attempt %d)", call_put, i + 1)
            continue

        if not isinstance(info_list, list) or not info_list:
            log.warning("  [async %s] urlForDetails empty payload", call_put)
            continue

        info       = info_list[0]
        meta       = info.get("meta", {})
        status_str = meta.get("status", "")

        if status_str != "COMPLETE":
            log.info("  [async %s] meta.status=%r (attempt %d) — waiting...", call_put, status_str, i + 1)
            continue

        data_entries = info.get("data", [])
        if not data_entries:
            log.warning("  [async %s] COMPLETE but data[] is empty", call_put)
            return []

        url_for_download = data_entries[0].get("urlForDownload")
        if not url_for_download:
            log.warning("  [async %s] COMPLETE but urlForDownload missing: %s", call_put, data_entries[0])
            return []

        log.debug("  [async %s] COMPLETE — urlForDownload obtained", call_put)
        break

    if not url_for_download:
        log.error("  [async %s] exhausted %d poll attempts — never reached COMPLETE",
                  call_put, len(ASYNC_INFO_POLL_DELAYS))
        return []

    # Hop 3: download and decompress gzip CSV
    try:
        r3 = session.get(url_for_download, timeout=120)
    except (requests.Timeout, requests.ConnectionError) as exc:
        log.error("  [async %s] urlForDownload request failed: %s", call_put, exc)
        return []

    if r3.status_code != 200:
        log.error("  [async %s] urlForDownload HTTP %s", call_put, r3.status_code)
        return []

    try:
        raw_bytes = gzip.decompress(r3.content)
    except OSError as exc:
        log.error("  [async %s] gzip decompress failed: %s", call_put, exc)
        return []

    rows = list(csv.DictReader(io.StringIO(raw_bytes.decode("utf-8"))))
    log.info("  [async %s] parsed %d contracts from gzip CSV", call_put, len(rows))
    return rows


def discover_contracts(
    session: requests.Session,
    api_key: str,
    symbol: str,
    date: str,
    exp_from: str,
    exp_to: str,
    strike_from: float,
    strike_to: float,
    call_put: str,    # "C" or "P"
    region: str,
    base_url: str = BASE_URL_DEFAULT,
    debug: bool = False,
    timeout: int = 60,
) -> list[dict]:
    """Discover all option contracts for one side (C or P) on a given date."""
    log = logging.getLogger("ivol.discovery")
    params = {
        "apiKey":     api_key,
        "symbol":     symbol,
        "date":       date,
        "expFrom":    exp_from,
        "expTo":      exp_to,
        "strikeFrom": strike_from,
        "strikeTo":   strike_to,
        "callPut":    call_put,
        "region":     region,
    }
    url = f"{base_url}{DISCOVERY_PATH}"

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)

        try:
            r = session.get(url, params=params, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == len(RETRY_DELAYS):
                log.error("[discovery %s] network error after %d attempts: %s", call_put, attempt, exc)
                return []
            continue

        if r.status_code == 200:
            body          = r.json()
            status        = body.get("status", {})
            data          = body.get("data") or []
            records_found = int(status.get("recordsFound") or 0)
            log.info("  %s: recordsFound=%d  len(data)=%d", call_put, records_found, len(data))

            if not data and records_found > 0:
                url_for_details = status.get("urlForDetails")
                if url_for_details:
                    log.info("  %s: async delivery triggered (recordsFound=%d)", call_put, records_found)
                    data = _fetch_async_discovery(session, url_for_details, call_put, log)

            return data

        if r.status_code in RETRY_STATUSES:
            log.warning("  [discovery %s] HTTP %s attempt %d", call_put, r.status_code, attempt)
            continue

        log.error("  [discovery %s] HTTP %s: %s", call_put, r.status_code, r.text[:300])
        return []

    return []
