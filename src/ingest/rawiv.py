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
    option_id: int,
    date: str,
    region: str,
    timeout: int = 60,
    poll_delay: float = 2.0,
    max_polls: int = 5,
) -> tuple[dict | None, str | None]:
    """Fetch raw-iv for one optionId.  Designed for concurrent worker threads.

    Returns:
        (row_dict, None)    — success with data
        (None, None)        — success, 0 rows (no data for this contract/date)
        (None, error_str)   — failed after all retries
    """
    session  = _thread_session()
    last_err: str | None = None

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)

        api_key = key_pool.acquire()

        try:
            r = session.get(
                f"{base_url}{RAWIV_PATH}",
                params={"apiKey": api_key, "optionId": option_id,
                        "from": date, "to": date, "region": region},
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
