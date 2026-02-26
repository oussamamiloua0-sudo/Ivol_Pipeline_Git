"""Export option EOD data to Excel (or TSV) matching the canonical 21-column spec.

Columns (exact order):
  trade_date, underlying, option_id, option_symbol, call_put, strike,
  expiration_date, dte, style, bid, ask, price, iv, preiv,
  delta, gamma, vega, theta, rho, volume, open_interest

Examples:
  # Single date, single symbol → Excel
  python scripts/export_excel.py --symbol SPX --date 2022-02-14

  # Date range, multiple symbols → Excel
  python scripts/export_excel.py --symbol SPX AAPL --start 2022-01-03 --end 2022-01-07

  # TSV output
  python scripts/export_excel.py --symbol AAPL --date 2022-01-03 --format tsv

  # Custom output path
  python scripts/export_excel.py --symbol SPX --date 2022-02-14 --out my_export.xlsx
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.db.engine import _load_env, get_engine   # noqa: E402
from src.export.query import fetch_export_df       # noqa: E402
from src.export.excel import write_excel, write_tsv  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol",  nargs="*", default=None,
                    help="Symbol(s) to export (default: all)")
    ap.add_argument("--date",    default=None, metavar="YYYY-MM-DD",
                    help="Single date (overrides --start/--end)")
    ap.add_argument("--start",   default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--end",     default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--out",     default=None,
                    help="Output file path (default: auto-generated)")
    ap.add_argument("--format",  choices=["xlsx", "tsv"], default="xlsx",
                    help="Output format (default: xlsx)")
    args = ap.parse_args()

    _load_env()

    db_url = os.getenv("DB_URL") or os.getenv("DATABASE_URL") or ""
    if not db_url:
        raise SystemExit("ERROR: DB_URL not set in .env")
    os.environ["DB_URL"] = db_url.strip().strip('"').strip("'")

    engine = get_engine()

    print("Querying database...")
    df = fetch_export_df(
        engine,
        symbols=args.symbol,
        date=args.date,
        start=args.start,
        end=args.end,
    )

    if df.empty:
        print("No rows returned — check your filters.")
        sys.exit(0)

    print(f"Rows: {len(df):,}")

    # Auto-generate output filename
    if args.out:
        out_path = args.out
    else:
        sym_part  = "_".join(s.upper() for s in args.symbol) if args.symbol else "ALL"
        date_part = args.date or f"{args.start}_to_{args.end}" or datetime.now().strftime("%Y%m%d")
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext       = args.format
        out_path  = f"{sym_part}_{date_part}_{ts}.{ext}"

    if args.format == "xlsx":
        out = write_excel(df, out_path)
    else:
        out = write_tsv(df, out_path)

    print(f"Saved: {out}  ({len(df):,} rows × {len(df.columns)} columns)")


if __name__ == "__main__":
    main()
