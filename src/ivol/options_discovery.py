from __future__ import annotations

import json
import requests

from config import Settings
from ivol.client import IvolClient


def call(url: str, params: dict) -> tuple[int, str, str]:
    r = requests.get(url, params=params, timeout=60)
    ctype = (r.headers.get("Content-Type") or "").lower()
    body = r.text
    return r.status_code, ctype, body


def pretty_json(text: str) -> str:
    try:
        obj = json.loads(text)
        return json.dumps(obj, indent=2)[:1200]
    except Exception:
        return text[:1200]


def try_endpoint(base: str, path: str, params_variants: list[dict]) -> bool:
    full = base.rstrip("/") + "/" + path.lstrip("/")
    print(f"\n=== {path} ===")
    ok_any = False

    for i, p in enumerate(params_variants, start=1):
        status, ctype, body = call(full, p)
        print(f"  try#{i} -> HTTP {status} | {ctype.split(';')[0]}")

        if status == 200:
            ok_any = True
            if "application/json" in ctype:
                print(pretty_json(body))
            else:
                # csv or other
                lines = body.splitlines()
                print("  (non-JSON) first lines:")
                print("\n".join(lines[:5]))
            break
        else:
            # show error message
            print("  error preview:")
            print(pretty_json(body))

    return ok_any


def main() -> None:
    s = Settings()
    c = IvolClient(s)
    token = c.get_token()

    base = s.ivol_base_url
    symbol = "AAPL"
    date = "2022-06-30"

    # Two common param styles:
    # 1) single-day using date=
    # 2) range using from= & to=
    common_variants = [
        {"token": token, "symbol": symbol, "date": date, "region": s.region},
        {"token": token, "symbol": symbol, "from": date, "to": date, "region": s.region},
    ]

    # Candidate endpoints (we’ll discover which ones exist in YOUR environment)
    candidates = [
        "/equities/eod/options-rawiv",
        "/equities/eod/option-rawiv",
        "/equities/eod/options_rawiv",
        "/equities/eod/option_rawiv",

        "/equities/eod/options-nbbo",
        "/equities/eod/option-nbbo",
        "/equities/eod/options_nbbo",
        "/equities/eod/option_nbbo",

        "/equities/eod/ivx",
        "/equities/eod/hv",
        "/equities/eod/historical-volatility",
    ]

    found = []
    for path in candidates:
        if try_endpoint(base, path, common_variants):
            found.append(path)

    print("\n==== SUMMARY ====")
    if not found:
        print("No options/vol endpoints succeeded with these candidates.")
        print("Next: we’ll widen the candidate list based on your 'Available Endpoints' page.")
    else:
        print("Working endpoints:")
        for p in found:
            print(" -", p)


if __name__ == "__main__":
    main()
