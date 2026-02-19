import argparse
import subprocess
import sys
from datetime import date, timedelta


def daterange(d1: date, d2: date):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--from_date", required=True)
    p.add_argument("--to_date", required=True)
    p.add_argument("--dte", required=True, type=int)
    p.add_argument("--callput", required=True, choices=["C", "P"])
    p.add_argument("--deltas", required=True, help="comma-separated, e.g. -0.10,-0.17,-0.30,-0.90")
    p.add_argument("--sleep", type=float, default=0.2)
    p.add_argument("--skip_weekends", action="store_true")
    args = p.parse_args()

    d1 = date.fromisoformat(args.from_date)
    d2 = date.fromisoformat(args.to_date)
    deltas = [float(x.strip()) for x in args.deltas.split(",") if x.strip()]

    days_ok = 0
    days_fail = 0

    for d in daterange(d1, d2):
        if args.skip_weekends and is_weekend(d):
            continue

        ds = d.isoformat()
        print(f"\n===== DAY {ds} =====")

        day_failed = False
        for de in deltas:
            cmd = [
                sys.executable,
                r".\src\jobs\load_options_from_nearest_to_db.py",
                "--symbol", args.symbol,
                "--date", ds,
                "--dte", str(args.dte),
                "--callput", args.callput,
                "--delta", str(de),
            ]
            print(f"--- delta {de} ---")
            rc = subprocess.call(cmd, shell=False)
            if rc != 0:
                day_failed = True
                print(f"FAILED delta {de} on {ds} (exit={rc})")

            if args.sleep and args.sleep > 0:
                import time
                time.sleep(args.sleep)

        if day_failed:
            days_fail += 1
        else:
            days_ok += 1

    print("\n=== MULTI-DELTA BACKFILL SUMMARY ===")
    print("days ok:", days_ok)
    print("days failed:", days_fail)


if __name__ == "__main__":
    main()
