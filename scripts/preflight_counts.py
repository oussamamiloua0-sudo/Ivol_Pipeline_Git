"""Preflight row-count check for a given trade date.

Shows how many rows already exist BEFORE a run (detect reruns vs fresh days)
and can be re-run AFTER ingest to show the delta.

Usage:
  # Check all symbols for 2026-02-24
  python scripts/preflight_counts.py --date 2026-02-24

  # Check specific symbols
  python scripts/preflight_counts.py --date 2026-02-24 --symbols SPX AAPL

  # Show the raw SQL queries used (for manual mysql session)
  python scripts/preflight_counts.py --date 2026-02-24 --show-sql

Output example:
  ============================================================
  PREFLIGHT COUNTS  date=2026-02-24
  ============================================================
  Queries run:
    SELECT COUNT(*) FROM fact_option_eod WHERE trade_date = '2026-02-24'
    ...

  Table                     Rows
  --------------------------  --------
  fact_option_eod              0
  fact_underlying_eod          0    (table not found)
  fact_vol_metrics             0    (table not found)

  Per-symbol breakdown (fact_option_eod):
    symbol   rows
    -------  ----
    (none)
  ============================================================
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.db.engine import _load_env, get_engine   # noqa: E402

from sqlalchemy import text   # noqa: E402


# Tables to check: (table_name, date_column)
_TABLES = [
    ("fact_option_eod",    "trade_date"),
    ("fact_underlying_eod","trade_date"),
    ("fact_vol_metrics",   "trade_date"),
]

# Per-symbol breakdown join
_SYMBOL_SQL = """
SELECT u.symbol, COUNT(f.option_id) AS rows
FROM fact_option_eod f
JOIN dim_option_contract c ON c.option_id     = f.option_id
JOIN dim_underlying u      ON u.underlying_id = c.underlying_id
WHERE f.trade_date = :date
  {symbol_clause}
GROUP BY u.symbol
ORDER BY u.symbol
"""


def count_rows(engine, table: str, date_col: str, date: str) -> tuple[int, str | None]:
    """Return (count, None) or (0, error_note)."""
    sql = f"SELECT COUNT(*) FROM {table} WHERE {date_col} = :date"
    try:
        with engine.connect() as conn:
            row = conn.execute(text(sql), {"date": date}).fetchone()
        return int(row[0]), None
    except Exception as exc:
        note = str(exc).split("\n")[0][:60]
        return 0, f"(error: {note})"


def symbol_breakdown(engine, date: str, symbols: list[str] | None) -> list[tuple[str, int]]:
    if symbols:
        placeholders = ", ".join(f":sym{i}" for i in range(len(symbols)))
        symbol_clause = f"AND u.symbol IN ({placeholders})"
        params: dict = {"date": date}
        for i, s in enumerate(symbols):
            params[f"sym{i}"] = s
    else:
        symbol_clause = ""
        params = {"date": date}

    sql = _SYMBOL_SQL.format(symbol_clause=symbol_clause)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [(r[0], int(r[1])) for r in rows]
    except Exception:
        return []


def print_report(engine, date: str, symbols: list[str] | None, show_sql: bool) -> dict[str, int]:
    """Print the counts report and return {table: count} for delta comparison."""
    line = "=" * 60

    print(line)
    print(f"PREFLIGHT COUNTS  date={date}")
    print(line)

    if show_sql:
        print("\nSQL queries:")
        for tbl, dcol in _TABLES:
            q = f"  SELECT COUNT(*) FROM {tbl} WHERE {dcol} = '{date}';"
            print(q)
        if symbols:
            sym_list = ", ".join(f"'{s}'" for s in symbols)
            print(f"  -- plus per-symbol breakdown WHERE u.symbol IN ({sym_list})")
        print()

    # Table-level counts
    print(f"  {'Table':<30}  {'Rows':>8}  Note")
    print(f"  {'-'*30}  {'-'*8}  ----")
    totals: dict[str, int] = {}
    for tbl, dcol in _TABLES:
        cnt, note = count_rows(engine, tbl, dcol, date)
        totals[tbl] = cnt
        note_str = f"  {note}" if note else ""
        print(f"  {tbl:<30}  {cnt:>8}{note_str}")

    # Per-symbol breakdown
    print()
    rows = symbol_breakdown(engine, date, symbols)
    if rows:
        print(f"  Per-symbol breakdown (fact_option_eod):")
        print(f"    {'symbol':<12}  {'rows':>8}")
        print(f"    {'-'*12}  {'-'*8}")
        for sym, cnt in rows:
            print(f"    {sym:<12}  {cnt:>8}")
    else:
        print("  Per-symbol breakdown: (none — no data for this date)")

    print(line)
    return totals


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--date",     required=True, metavar="YYYY-MM-DD",
                    help="Trade date to check")
    ap.add_argument("--symbols",  nargs="*", default=None, metavar="SYM",
                    help="Filter by symbol(s) for breakdown (e.g. SPX AAPL)")
    ap.add_argument("--show-sql", action="store_true", dest="show_sql",
                    help="Print the SQL queries being run")
    args = ap.parse_args()

    _load_env()

    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL") or ""
    if not db_url:
        raise SystemExit("ERROR: DB_URL not set in .env")
    os.environ["DB_URL"] = db_url.strip().strip('"').strip("'")

    symbols = [s.upper() for s in args.symbols] if args.symbols else None

    engine = get_engine()
    print_report(engine, args.date, symbols, args.show_sql)


if __name__ == "__main__":
    main()
