import os
import sys
from pathlib import Path

# Add project root and ./src to PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
            value = value[1:-1]
        os.environ[key] = value


def _clean_env(key: str, default: str = "") -> str:
    v = os.getenv(key, default)
    return v.strip() if v else default


def _ssl_args() -> dict:
    ssl_mode = _clean_env("DB_SSL_MODE", "REQUIRED").upper()
    ca_path = _clean_env("DB_SSL_CA", "")
    if ssl_mode == "DISABLED":
        return {}
    if ca_path and Path(ca_path).exists():
        return {"ssl": {"ca": ca_path}}
    return {"ssl": {}}


def _pymysql_ping() -> None:
    try:
        import pymysql
    except ModuleNotFoundError:
        _tcp_ping_only()
        return

    host = _clean_env("DB_HOST", "")
    port = int(_clean_env("DB_PORT", "3306"))
    user = _clean_env("DB_USER", "")
    password = _clean_env("DB_PASSWORD", "")
    db = _clean_env("DB_NAME", "")
    connect_timeout = int(_clean_env("DB_CONNECT_TIMEOUT", "15"))

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db,
        connect_timeout=connect_timeout,
        read_timeout=60,
        write_timeout=60,
        autocommit=True,
        **_ssl_args(),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT NOW() AS now, CURRENT_USER() AS whoami")
            print(cur.fetchall())
            cur.execute("SHOW STATUS LIKE 'Ssl_cipher'")
            print(cur.fetchall())
    finally:
        conn.close()


def _tcp_ping_only() -> None:
    import socket

    host = _clean_env("DB_HOST", "")
    port = int(_clean_env("DB_PORT", "3306"))
    timeout_s = int(_clean_env("DB_CONNECT_TIMEOUT", "15"))

    with socket.create_connection((host, port), timeout=timeout_s):
        print([("tcp_connect", "ok")])


def main() -> None:
    _load_env()
    try:
        from sqlalchemy import text
        from src.db.engine import get_engine

        eng = get_engine()
        with eng.connect() as c:
            print(c.execute(text("SELECT NOW() AS now, CURRENT_USER() AS whoami")).all())
            print(c.execute(text("SHOW STATUS LIKE 'Ssl_cipher';")).all())
    except ModuleNotFoundError as exc:
        if exc.name != "sqlalchemy":
            raise
        _pymysql_ping()


if __name__ == "__main__":
    main()
