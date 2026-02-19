from __future__ import annotations

import requests
from config import Settings


class IvolClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.http = requests.Session()

    def get_token(self) -> str:
        if not self.s.ivol_username or not self.s.ivol_password:
            raise RuntimeError("Set IVOL_USERNAME and IVOL_PASSWORD in .env")

        url = self.s.ivol_base_url.rstrip("/") + "/token/get"
        r = self.http.get(
            url,
            params={"username": self.s.ivol_username, "password": self.s.ivol_password},
            timeout=60,
        )
        if not r.ok:
            raise RuntimeError(f"Token request failed HTTP {r.status_code}\n{r.text[:800]}")
        return r.text.strip().strip('"')

    def get_json(self, path: str, params: dict) -> dict:
        url = self.s.ivol_base_url.rstrip("/") + "/" + path.lstrip("/")
        r = self.http.get(url, params=params, timeout=120)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code} for {r.url}\n{r.text[:1200]}")
        return r.json()

    def stock_prices(self, token: str, symbol: str, date_from: str, date_to: str) -> dict:
        # Confirmed working in your environment:
        # /equities/eod/stock-prices
        return self.get_json(
            "/equities/eod/stock-prices",
            {
                "token": token,
                "symbol": symbol,
                "from": date_from,
                "to": date_to,
                "region": self.s.region,
            },
        )
