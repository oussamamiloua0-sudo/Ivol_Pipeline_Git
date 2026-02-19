import os
from datetime import date, timedelta
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


def main() -> None:
    load_env()
    api_key = (os.getenv("IVOL_API_KEY") or "").strip()
    if not api_key:
        print("Missing IVOL_API_KEY")
        return
    base_url = (os.getenv("IVOL_BASE_URL", BASE_URL_DEFAULT) or BASE_URL_DEFAULT).rstrip("/")

    symbols_file = Path(__file__).resolve().parents[1] / "data" / "symbols.txt"
    symbols = [s.strip().upper() for s in symbols_file.read_text(encoding="utf-8").splitlines() if s.strip()]

    run_date = (date.today() - timedelta(days=1)).isoformat()

    ok = []
    no_data = []
    blocked = []
    errors = []

    for sym in symbols:
        params = {"apiKey": api_key, "symbol": sym, "date": run_date}
        try:
            r = requests.get(f"{base_url}/equities/eod/stock-prices", params=params, timeout=30)
            if r.status_code == 403:
                blocked.append(sym)
                continue
            if r.status_code != 200:
                errors.append(sym)
                continue
            j = r.json()
            status = j.get("status", {})
            records = status.get("recordsFound")
            if records and int(records) > 0:
                ok.append(sym)
            else:
                no_data.append(sym)
        except Exception:
            errors.append(sym)

    print("date:", run_date)
    print("ok:", ",".join(ok))
    print("no_data:", ",".join(no_data))
    print("blocked:", ",".join(blocked))
    print("errors:", ",".join(errors))


if __name__ == "__main__":
    main()
