"""Shared iVolatility API constants used across discovery and ingest."""
from __future__ import annotations

BASE_URL_DEFAULT = "https://restapi.ivolatility.com"
DISCOVERY_PATH   = "/equities/eod/option-series-on-date"
RAWIV_PATH       = "/equities/eod/single-stock-option-raw-iv"
STOCK_PRICES_PATH = "/equities/eod/stock-prices"

# Rows per bulk INSERT chunk.  1000 rows × ~20 cols × ~10 bytes ≈ 200 KB, well
# under MySQL's default max_allowed_packet (16 MB).
BULK_CHUNK_SIZE = 1000

# Keys iVol may use for async / big-result download links.
LINK_KEYS = ("link", "url", "downloadUrl", "fileUrl", "downloadLink", "urlForDetails")

# Retryable HTTP statuses.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_DELAYS   = [0, 1, 2, 4, 8, 16]   # seconds; index 0 = no sleep before first attempt

# Polling delays for the async 3-hop discovery path (urlForDetails).
# Total budget ~45 s before giving up.
ASYNC_INFO_POLL_DELAYS = [0, 3, 6, 12, 24]   # seconds
