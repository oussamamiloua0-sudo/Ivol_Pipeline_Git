from __future__ import annotations

import json
import requests

from config import Settings
from ivol.client import IvolClient


def call(url: str, params: dict) -> tuple[int, str, str]:
    r = requests.get(url, params=params, timeout=120)
    ctype = (r.headers.get("Content-Type") or "").lower()
    return r.status_code, ctype, r.text


def pretty(text: str) -> str:
    try:
        obj = json.loads(text)
        return json.dumps(obj, indent=2)[:1200]
    except Exception:
        return text[:1200]


def try_endpoint(base: str, path: str, variants: list[dict]) -> bool:
    full = base.rstrip("/") + "/" + path.lstrip("/")
    print(f"\n=== {path} ===")
    for i, p in enumerate(variants, start=1):
        status, ctype, body = call(full, p)
        print(f"  try#{i} -> HTTP {status} | {ctype.split(';')[0]}")
        if status == 200:
            if "application/json" in ctype:
                print(pretty(body))
            else:
                lines = body.splitlines()
                print("  (non-JSON) first 5 lines:")
                print("\n".join(lines[:5]))
            return True
        else:
            print("  error preview:")
            print(pretty(body))
    return False


def main() -> None:
    s = Settings()
    token = IvolClient(s).get_token()

    base = s.ivol_base_url
    symbol = "AAPL"
    date = "2022-06-30"

    # dd endpoints sometimes accept username/password + token (docs list them) :contentReference[oaicite:1]{index=1}
    variants = [
        # token only
        {"token": token, "symbol": symbol, "date": date},
        # username/password + token
        {"username": s.ivol_username, "password": s.ivol_password, "token": token, "symbol": symbol, "date": date},
        # token only (range style)
        {"token": token, "symbol": symbol, "from": date, "to": date},
        # username/password + token (range style)
        {"username": s.ivol_username, "password": s.ivol_password, "token": token, "symbol": symbol, "from": date, "to": date},
    ]

    # Candidates based on iVol “dd” endpoint naming shown in their guides :contentReference[oaicite:2]{index=2}
    candidates = [
        "/dd/eod/equity/prices",
        "/dd/eod/equity/options",
        "/dd/eod/equity/ivx",
        "/dd/eod/equity/hv",
        "/dd/eod/equity/ivs",
        "/dd/eod/equity/nsoptions",
    ]

    working = []
    for path in candidates:
        if try_endpoint(base, path, variants):
            working.append(path)

    print("\n==== SUMMARY ====")
    if not working:
        print("No dd EOD endpoints worked with these candidates.")
    else:
        print("Working dd EOD endpoints:")
        for p in working:
            print(" -", p)


if __name__ == "__main__":
    main()
