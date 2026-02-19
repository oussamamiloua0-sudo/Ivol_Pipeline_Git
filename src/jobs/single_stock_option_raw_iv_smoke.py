import os
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

BASE_URL_DEFAULT = "https://restapi.ivolatility.com"


def load_env():
    # project root: .../ivolatility-data-pipeline
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path, override=True)
    return env_path


def safe_params(params: dict) -> dict:
    out = dict(params)
    if "apiKey" in out and out["apiKey"]:
        out["apiKey"] = "****"
    return out


def try_request(base_url: str, params: dict):
    url = f"{base_url}/equities/eod/single-stock-option-raw-iv"
    r = requests.get(url, params=params, timeout=60)
    return r


def main():
    env_path = load_env()

    p = argparse.ArgumentParser()
    p.add_argument("--option_id", required=True, type=int)
    p.add_argument("--from_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--to_date", required=True, help="YYYY-MM-DD")
    args = p.parse_args()

    base_url = os.getenv("IVOL_BASE_URL", BASE_URL_DEFAULT).rstrip("/")
    api_key = (os.getenv("IVOL_API_KEY") or "").strip()

    if not api_key:
        raise RuntimeError("Missing IVOL_API_KEY in .env")

    # Try both possible parameter names to avoid doc ambiguity
    # (we'll keep the one that works)
    candidates = [
        {"apiKey": api_key, "optionId": args.option_id, "from": args.from_date, "to": args.to_date},
        {"apiKey": api_key, "option_id": args.option_id, "from": args.from_date, "to": args.to_date},
    ]

    print(f".env loaded: {env_path} (exists={env_path.exists()})")
    print("BASE:", base_url)
    print("")

    last = None
    for i, params in enumerate(candidates, start=1):
        print(f"=== Attempt {i} params ===")
        print(safe_params(params))

        r = try_request(base_url, params)
        print("HTTP", r.status_code)

        try:
            j = r.json()
        except Exception:
            print(r.text[:1500])
            r.raise_for_status()
            return

        status = j.get("status", {})
        data = j.get("data", [])
        print("STATUS:", status)
        print("ROWS:", len(data))

        # If this attempt clearly worked, show first row and stop
        if r.status_code == 200 and isinstance(data, list) and len(data) > 0:
            print("FIRST ROW KEYS:", list(data[0].keys()))
            print("FIRST ROW SAMPLE:", data[0])
            return

        last = (r, j)

        print("")

    # If none returned rows, print last status to help decide next move
    if last:
        _, j = last
        print("No rows returned. Last STATUS:", j.get("status", {}))
        print("Last QUERY:", j.get("query", {}))


if __name__ == "__main__":
    main()
