import requests
from config import Settings

def main():
    s = Settings()
    url = s.ivol_base_url.rstrip("/") + "/token/get"

    if not s.ivol_username or not s.ivol_password:
        raise SystemExit("Set IVOL_USERNAME and IVOL_PASSWORD in .env first.")

    r = requests.get(url, params={"username": s.ivol_username, "password": s.ivol_password}, timeout=60)
    print("status:", r.status_code)
    print("response:", r.text[:200])

if __name__ == "__main__":
    main()
