from __future__ import annotations

from config import Settings
from ivol.client import IvolClient


def try_endpoint(client: IvolClient, token: str, path: str) -> bool:
    s = client.s
    params = {
        "token": token,
        "symbol": "AAPL",
        "from": "2022-06-30",
        "to": "2022-06-30",
        # region not always required; include if your entitlement expects it
        "region": s.region,
    }

    print(f"\n--- Trying {path} ---")
    try:
        res = client.get(path, params)
    except Exception as e:
        print("FAILED:", e)
        return False

    if isinstance(res, str):
        lines = res.splitlines()
        print("OK (CSV). First 5 lines:")
        print("\n".join(lines[:5]))
        return True

    print("OK (JSON). status:", res.get("status"))
    data = res.get("data", [])
    print("rows:", len(data))
    if data:
        print("first row keys:", list(data[0].keys()))
        print("first row sample:", data[0])
    return True


def main() -> None:
    s = Settings()
    c = IvolClient(s)
    token = c.get_token()

    # iVol docs appear in the wild with both variants
    # We'll try both and stop at the first that works.
    candidates = [
        "/equities/eod/stocks-prices",
        "/equities/eod/stock-prices",
    ]

    for path in candidates:
        if try_endpoint(c, token, path):
            print("\nSUCCESS ✅")
            return

    raise SystemExit("\nAll candidate endpoints failed. We need to adjust to your entitlement/endpoint names.")


if __name__ == "__main__":
    main()
