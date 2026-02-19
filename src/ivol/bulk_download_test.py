from __future__ import annotations

import time
from pathlib import Path

import requests

from config import Settings
from ivol.client import IvolClient


def fetch_json(session: requests.Session, url: str, params: dict) -> dict:
    r = session.get(url, params=params, timeout=120)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code} for {r.url}\n{r.text[:1200]}")
    return r.json()


def download_file(session: requests.Session, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = session.get(url, timeout=300, stream=True)
    if not r.ok:
        raise RuntimeError(f"Download failed HTTP {r.status_code} for {url}\n{r.text[:1200]}")
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)


def main() -> None:
    s = Settings()
    c = IvolClient(s)
    token = c.get_token()

    base = s.ivol_base_url.rstrip("/")
    session = requests.Session()

    # Bulk download endpoint (we’ll see if it’s enabled for your account)
    endpoint = base + "/data-download-ui"

    symbol = "AAPL"
    date_from = "2022-06-30"
    date_to = "2022-06-30"

    # Try likely dataset IDs first (if one is wrong, server will usually say so)
    # These names are consistent with iVol's dataset naming (RawIV, NBBO).
    data_types_to_try = [
        "EOD_EQUITY_RAWIV",     # IV + Greeks per contract
        "EOD_EQUITY_OPTNBBO",   # options prices (NBBO) + volume + OI
    ]

    for data_type in data_types_to_try:
        print(f"\n=== Trying data-download-ui dataType={data_type} ===")
        params = {
            "token": token,
            "dataType": data_type,
            "symbol": symbol,
            "from": date_from,
            "to": date_to,
            "region": s.region,
        }

        try:
            res = fetch_json(session, endpoint, params)
        except Exception as e:
            print("FAILED:", e)
            continue

        status = res.get("status", {})
        print("status:", status)

        code = (status.get("code") or "").upper()
        url_for_details = status.get("urlForDetails")

        # If complete and data inline (rare for bulk), show count
        if res.get("data"):
            print("inline rows:", len(res["data"]))
            print("first row keys:", list(res["data"][0].keys())[:25])
            continue

        # If async file is prepared
        if url_for_details:
            print("urlForDetails:", url_for_details)
            # Poll until file is ready (some responses stay PENDING for a bit)
            for attempt in range(1, 16):
                try:
                    res2 = fetch_json(session, base + "/" + url_for_details.lstrip("/"), {"token": token})
                    st2 = res2.get("status", {})
                    print(f"  poll {attempt}: {st2}")
                    code2 = (st2.get("code") or "").upper()
                    if code2 != "PENDING" and st2.get("urlForDetails"):
                        url_for_details = st2["urlForDetails"]
                        break
                    if code2 != "PENDING" and st2.get("code") in ("COMPLETE", "DONE"):
                        break
                except Exception:
                    # Sometimes urlForDetails is directly a file url; if JSON polling fails, break
                    break
                time.sleep(2)

            # Attempt direct download from urlForDetails (often a file url)
            try:
                file_url = url_for_details
                if not (file_url.startswith("http://") or file_url.startswith("https://")):
                    file_url = base + "/" + file_url.lstrip("/")
                out = Path(s.raw_dir) / "bulk_test" / f"{data_type}_{symbol}_{date_from}_{date_to}.csv.gz"
                download_file(session, file_url, out)
                print("downloaded:", out)
            except Exception as e:
                print("download attempt failed:", e)

        # If forbidden, it means bulk tool exists but not enabled
        if code == "FORBIDDEN" or "forbidden" in (status.get("name", "") or "").lower():
            print("Looks like this bulk dataset is not entitled for your account.")

    print("\nDONE.")


if __name__ == "__main__":
    main()
