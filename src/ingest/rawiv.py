"""Per-contract raw-iv fetcher — runs inside worker threads."""
from __future__ import annotations

import threading
import time

import requests

from src.ivol.constants import LINK_KEYS, RAWIV_PATH, RETRY_DELAYS, RETRY_STATUSES
from src.ivol.key_pool import KeyPool

_tls = threading.local()


def _thread_session() -> requests.Session:
    """One requests.Session per thread (not thread-safe to share)."""
    if not hasattr(_tls, "session"):
        _tls.session = requests.Session()
    return _tls.session


def fetch_rawiv(
    *,
    key_pool: KeyPool,
    base_url: str,
    option_id: int | None,
    date: str,
    region: str,
    occ_symbol: str | None = None,
    timeout: int = 60,
    poll_delay: float = 2.0,
    max_polls: int = 5,
) -> tuple[dict | None, str | None]:
    """Fetch raw-iv for one contract.  Designed for concurrent worker threads.

    Pass option_id for 2018+ data (real iVol ID).
    Pass occ_symbol for pre-2018 data (e.g. 'IWM   180316C00150000').
    option_id takes precedence if both are provided.

    Returns:
        (row_dict, None)    — success with data
        (None, None)        — success, 0 rows (no data for this contract/date)
        (None, error_str)   — failed after all retries
    """
    if option_id is None and occ_symbol is None:
        return None, "fetch_rawiv: both option_id and occ_symbol are None"
    session  = _thread_session()
    last_err: str | None = None

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)

        api_key = key_pool.acquire()

        params: dict = {"apiKey": api_key, "from": date, "to": date, "region": region}
        if option_id is not None:
            params["optionId"] = option_id
        else:
            params["symbol"] = occ_symbol

        try:
            r = session.get(
                f"{base_url}{RAWIV_PATH}",
                params=params,
                timeout=timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            continue

        if r.status_code == 429:
            key_pool.record_429()
            last_err = "HTTP 429"
            continue

        if r.status_code in RETRY_STATUSES - {429}:
            last_err = f"HTTP {r.status_code}"
            continue

        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"   # non-retryable

        key_pool.record_success()

        try:
            body = r.json()
        except ValueError:
            return None, "invalid JSON"

        # Async / big-result link response — poll until data arrives.
        for link_key in LINK_KEYS:
            if link_key in body:
                poll_url = body[link_key]
                for _ in range(max_polls):
                    time.sleep(poll_delay)
                    try:
                        pr = session.get(poll_url, timeout=timeout)
                    except Exception:
                        break
                    if pr.status_code == 200:
                        pdata = pr.json().get("data") or []
                        return (pdata[0] if pdata else None), None
                return None, f"async poll exhausted for '{link_key}'"

        data = body.get("data") or []
        return (data[0] if data else None), None

    return None, last_err or "max retries exceeded"
