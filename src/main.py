import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.jobs.load_options_from_nearest_to_db import main as run_load_options
from src.jobs.daily_ingest import main as run_daily_ingest

def main():
    parser = argparse.ArgumentParser(description="iVolatility Data Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command")

    # 'run' command
    run_parser = subparsers.add_parser("run", help="Run a job")
    run_subparsers = run_parser.add_subparsers(dest="job")

    # 'run load_options' command
    load_options_parser = run_subparsers.add_parser("load_options", help="Load options data")
    load_options_parser.add_argument("--symbol", required=True)
    load_options_parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    load_options_parser.add_argument("--dte", required=True, type=int)
    load_options_parser.add_argument("--callput", required=True, choices=["C", "P"])
    g = load_options_parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--delta", type=float)
    g.add_argument("--moneyness", type=int)

    # 'run daily_ingest' command
    daily_ingest_parser = run_subparsers.add_parser("daily_ingest", help="Run the daily ingest job")
    daily_ingest_parser.add_argument("--symbols", required=True, help="comma-separated, e.g. TQQQ,SPY,AAPL")
    daily_ingest_parser.add_argument("--dte", required=True, type=int)
    daily_ingest_parser.add_argument("--callput", required=True, choices=["C", "P"])
    daily_ingest_parser.add_argument("--deltas", required=True, help="comma-separated, e.g. -0.10,-0.17,-0.30")
    daily_ingest_parser.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")

    args = parser.parse_args()

    if args.command == "run":
        if args.job == "load_options":
            # Simulate the command-line arguments for the script
            sys.argv = [
                "src/jobs/load_options_from_nearest_to_db.py",
                "--symbol",
                args.symbol,
                "--date",
                args.date,
                "--dte",
                str(args.dte),
                "--callput",
                args.callput,
            ]
            if args.delta is not None:
                sys.argv.extend(["--delta", str(args.delta)])
            if args.moneyness is not None:
                sys.argv.extend(["--moneyness", str(args.moneyness)])

            run_load_options()
        elif args.job == "daily_ingest":
            # Simulate the command-line arguments for the script
            sys.argv = [
                "src/jobs/daily_ingest.py",
                "--symbols",
                args.symbols,
                "--dte",
                str(args.dte),
                "--callput",
                args.callput,
                "--deltas",
                args.deltas,
            ]
            if args.date:
                sys.argv.extend(["--date", args.date])

            run_daily_ingest()

if __name__ == "__main__":
    main()
