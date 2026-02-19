import argparse
import subprocess
import sys
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src.jobs.load_underlying_eod_once import run as run_underlying_eod
from src.jobs.load_vol_metrics_once import run as run_vol_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", required=True, help="comma-separated, e.g. TQQQ,SPY,AAPL")
    p.add_argument("--dte", required=True, type=int)
    p.add_argument("--callput", required=True, choices=["C", "P"])
    p.add_argument("--deltas", required=True, help="comma-separated, e.g. -0.10,-0.17,-0.30")
    p.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    args = p.parse_args()

    run_date = args.date or (date.today() - timedelta(days=1)).isoformat()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    deltas = [float(x.strip()) for x in args.deltas.split(",") if x.strip()]

    print("DAILY INGEST date:", run_date)
    print("symbols:", symbols)
    print("deltas:", deltas)
    print("dte:", args.dte, "callput:", args.callput)

    failed = 0
    total = 0

    for sym in symbols:
        try:
            print(f"\nUNDERLYING EOD: {sym} {run_date}")
            run_underlying_eod(sym, run_date)
        except Exception as exc:
            failed += 1
            print(f"FAILED underlying EOD {sym}: {exc}")

        try:
            print(f"\nVOL METRICS: {sym} {run_date}")
            run_vol_metrics(sym, run_date)
        except Exception as exc:
            failed += 1
            print(f"FAILED vol metrics {sym}: {exc}")

        for de in deltas:
            total += 1
            cmd = [
                sys.executable,
                r".\src\jobs\load_options_from_nearest_to_db.py",
                "--symbol", sym,
                "--date", run_date,
                "--dte", str(args.dte),
                "--callput", args.callput,
                "--delta", str(de),
            ]
            print(f"\nRUN {sym} delta={de}")
            rc = subprocess.call(cmd, shell=False)
            if rc != 0:
                failed += 1
                print(f"FAILED {sym} delta={de} exit={rc}")

    print("\n=== DAILY INGEST SUMMARY ===")
    print("date:", run_date)
    print("runs:", total)
    print("failed:", failed)


if __name__ == "__main__":
    main()

