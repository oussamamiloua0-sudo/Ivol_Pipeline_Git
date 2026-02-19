import os
import argparse
import requests
from pathlib import Path

from dotenv import load_dotenv  # python-dotenv installed


BASE_URL_DEFAULT = "https://restapi.ivolatility.com"


def mask(v: str | None) -> str:
    if not v:
        return "MISSING"
    v = str(v)
    if len(v) <= 8:
        return "SET(****)"
    return f"SET({v[:4]}…{v[-4:]})"


def find_env_file(start: Path) -> Path | None:
    # walk upward until we find a .env
    for p in [start, *start.parents]:
        candidate = p / ".env"
        if candidate.exists():
            return candidate
    return None


def get_token(base_url: str, username: str, password: str) -> str:
    r = requests.get(
        f"{base_url}/token/get",
        params={"username": username, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    token = j.get("token") or j.get("data") or j.get("Token") or j.get("result")
    if not token or not isinstance(token, str):
        raise RuntimeError(f"Could not parse token from response: {j}")
    return token


def main():
    # Load .env (override=True is the KEY fix)
    env_path = find_env_file(Path(__file__).resolve().parent)
    if env_path:
        load_dotenv(env_path, override=True)

    base_url = os.getenv("IVOL_BASE_URL", BASE_URL_DEFAULT).rstrip("/")
    api_key = (os.getenv("IVOL_API_KEY") or "").strip() or None
    username = (os.getenv("IVOL_USERNAME") or "").strip() or None
    password = (os.getenv("IVOL_PASSWORD") or "").strip() or None
    token = (os.getenv("IVOL_TOKEN") or "").strip() or None

    # SAFE DEBUG
    print(f".env: {env_path} (exists={bool(env_path and env_path.exists())})")
    print("IVOL_BASE_URL:", base_url)
    print("IVOL_API_KEY:", mask(api_key))
    print("IVOL_TOKEN:  ", mask(token))
    print("IVOL_USERNAME:", "SET" if username else "MISSING")
    print("IVOL_PASSWORD:", "SET" if password else "MISSING")
    print("")

    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD (used as startingDate)")
    p.add_argument("--dte", required=True, type=int)
    p.add_argument("--callput", required=True, choices=["C", "P"])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--moneyness", type=int)
    g.add_argument("--delta", type=float)
    args = p.parse_args()

    # Auth params (prefer apiKey, else token, else username/password -> token)
    auth_params = {}
    if api_key:
        auth_params["apiKey"] = api_key
    else:
        if not token and username and password:
            token = get_token(base_url, username, password)
        if token:
            auth_params["token"] = token
        else:
            raise RuntimeError(
                "No auth found after loading .env.\n"
                "Put IVOL_API_KEY=... in .env (preferred) or IVOL_USERNAME/IVOL_PASSWORD.\n"
            )

    # Delta sign normalization
    if args.delta is not None:
        if args.callput == "P" and args.delta > 0:
            args.delta = -abs(args.delta)
        if args.callput == "C" and args.delta < 0:
            args.delta = abs(args.delta)

    params = {
        **auth_params,
        "symbol": args.symbol,
        "startingDate": args.date,
        "dte": args.dte,
        "callPut": args.callput,
    }
    if args.moneyness is not None:
        params["moneyness"] = args.moneyness
    else:
        params["delta"] = args.delta

    url = f"{base_url}/equities/eod/nearest-option-tickers"
    r = requests.get(url, params=params, timeout=60)

    print("REQUEST URL:", r.url)
    print("HTTP", r.status_code)

    j = r.json()
    print("STATUS:", j.get("status", {}))
    data = j.get("data", [])
    print("ROWS:", len(data))
    for i, row in enumerate(data[:5]):
        print(f"[{i}] {row}")


if __name__ == "__main__":
    main()
