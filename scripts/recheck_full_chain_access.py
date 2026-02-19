import json
import os
import sys
from pathlib import Path

import requests

BASE_URL_DEFAULT = "https://restapi.ivolatility.com"


def find_env_file(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        candidate = p / ".env"
        if candidate.exists():
            return candidate
    return None


def load_env() -> None:
    env_path = find_env_file(Path(__file__).resolve().parent)
    if not env_path:
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def snippet(text: str, limit: int = 300) -> str:
    s = text.replace("\n", " ").replace("\r", " ")
    return s[:limit]


def main() -> None:
    load_env()
    base_url = (os.getenv("IVOL_BASE_URL", BASE_URL_DEFAULT) or BASE_URL_DEFAULT).rstrip("/")
    api_key = (os.getenv("IVOL_API_KEY") or "").strip()
    if not api_key:
        print("Missing IVOL_API_KEY in .env")
        sys.exit(1)

    symbol = "AAPL"
    date = "2022-06-30"

    endpoints = [
        "/equities/eod/options-rawiv",
        "/equities/eod/options-nbbo",
    ]

    for ep in endpoints:
        params = {"symbol": symbol, "date": date, "apiKey": api_key}
        r = requests.get(f"{base_url}{ep}", params=params, timeout=60)
        try:
            payload = r.json()
        except Exception:
            payload = {"_raw": r.text}

        status = payload.get("status") if isinstance(payload, dict) else {}
        records_found = None
        if isinstance(status, dict):
            records_found = status.get("recordsFound")

        safe_params = {"symbol": symbol, "date": date}
        print("endpoint:", ep)
        print("params:", safe_params)
        print("http:", r.status_code)
        print("recordsFound:", records_found)
        print("snippet:", snippet(json.dumps(payload, ensure_ascii=True) if isinstance(payload, dict) else str(payload)))
        print("---")


if __name__ == "__main__":
    main()
