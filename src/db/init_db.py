from pathlib import Path
from sqlalchemy import text
from db.engine import get_engine

def main() -> None:
    engine = get_engine()

    schema_path = Path(__file__).with_name("schema.sql")
    schema_sql = schema_path.read_text(encoding="utf-8")

    with engine.begin() as conn:
        conn.execute(text(schema_sql))

    print("DB schema created/verified.")

if __name__ == "__main__":
    main()
