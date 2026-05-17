import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        db_url = os.environ['DB_URL']
        _engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=3600)
    return _engine

def query(sql: str, params: dict = None):
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params or {})
        return result.mappings().all()
