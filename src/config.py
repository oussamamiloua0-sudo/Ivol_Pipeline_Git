from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    ivol_base_url: str = os.getenv("IVOL_BASE_URL", "http://restapi.ivolatility.com")
    ivol_username: str | None = os.getenv("IVOL_USERNAME")
    ivol_password: str | None = os.getenv("IVOL_PASSWORD")
    ivol_token: str | None = os.getenv("IVOL_TOKEN")

    db_url: str = os.getenv("DB_URL", "")
    symbols_file: str = os.getenv("SYMBOLS_FILE", "data/symbols.txt")
    raw_dir: str = os.getenv("RAW_DIR", "data/raw")
    log_dir: str = os.getenv("LOG_DIR", "logs")
    region: str = os.getenv("REGION", "USA")
