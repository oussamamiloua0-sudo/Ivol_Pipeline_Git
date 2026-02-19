# src/db/engine.py
from __future__ import annotations

import os
import socket
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

_ENGINE: Engine | None = None


def _project_root() -> Path:
    # engine.py is at src/db/engine.py -> project root is two levels up from src/
    return Path(__file__).resolve().parents[2]


def _load_env() -> None:
    """
    Always load the project's .env with override=True so we don't keep stale values.
    Works even if scripts run from a different working directory.
    """
    env_path = _project_root() / ".env"
    if load_dotenv is not None:
        load_dotenv(dotenv_path=env_path, override=True)
        return

    # Minimal .env loader when python-dotenv isn't installed.
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


def _clean_env_value(key: str, default: str = "") -> str:
    v = os.getenv(key, default)
    if v is None:
        return default
    v = v.strip()
    # strip wrapping quotes if present
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        v = v[1:-1]
    return v


def _ssl_connect_args() -> dict:
    """
    DO Managed MySQL + REQUIRE SSL users:
    - Always enforce TLS unless DB_SSL_MODE=DISABLED
    - If DB_SSL_CA exists, pass it (safe for REQUIRED too)
    """
    ssl_mode = _clean_env_value("DB_SSL_MODE", "REQUIRED").upper()
    ca_path = _clean_env_value("DB_SSL_CA", "")

    connect_args: dict = {
        "connect_timeout": int(_clean_env_value("DB_CONNECT_TIMEOUT", "15")),
        "read_timeout": int(_clean_env_value("DB_READ_TIMEOUT", "60")),
        "write_timeout": int(_clean_env_value("DB_WRITE_TIMEOUT", "60")),
    }

    if ssl_mode != "DISABLED":
        # Force SSL at minimum
        if ca_path:
            ca_file = Path(ca_path)
            if ca_file.exists():
                connect_args["ssl"] = {"ca": str(ca_file)}
            else:
                connect_args["ssl"] = {}
        else:
            connect_args["ssl"] = {}

    return connect_args


def _db_host_port(db_url: str) -> tuple[str, int]:
    host = _clean_env_value("DB_HOST", "")
    port_str = _clean_env_value("DB_PORT", "")
    if host:
        try:
            port = int(port_str) if port_str else 3306
        except ValueError:
            port = 3306
        return host, port

    url = make_url(db_url)
    return url.host or "localhost", url.port or 3306


def _tcp_preflight(host: str, port: int, timeout_s: int) -> None:
    """
    Fast network reachability check to surface actionable errors before SQLAlchemy.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return
    except OSError as exc:
        raise RuntimeError(
            "TCP connect failed. Check DO Trusted Sources, firewall/VPN, and host/port."
        ) from exc


def get_engine(*, echo: bool = False) -> Engine:
    """
    Central engine factory. All jobs must use this so SSL is always applied.
    """
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    _load_env()

    db_url = _clean_env_value("DB_URL", "")
    if not db_url:
        raise RuntimeError("DB_URL is missing. Set DB_URL in .env (DO host:25060).")

    preflight = _clean_env_value("DB_TCP_PREFLIGHT", "1").upper()
    if preflight not in {"0", "FALSE", "NO", "DISABLED"}:
        host, port = _db_host_port(db_url)
        _tcp_preflight(host, port, int(_clean_env_value("DB_CONNECT_TIMEOUT", "15")))

    _ENGINE = create_engine(
        db_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=_ssl_connect_args(),
        future=True,
    )
    return _ENGINE
