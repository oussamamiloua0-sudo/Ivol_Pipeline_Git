import argparse
from datetime import date, timedelta
import subprocess
import sys
import time
import json
from pathlib import Path


def daterange(d1: date, d2: date):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5=Sat, 6=Sun


def load_progress(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_progress(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--from_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--to_date", required=True, help="YYYY-MM-DD")
    p.add_argument("--dte", required=True, type=int)
    p.add_argument("--callput", required=True, choices=["C", "P"])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--delta", type=float)
    g.add_argument("--moneyness", type=int)

    p.add_argument("--sleep", type=float, default=0.25, help="seconds to sleep between days (default 0.25)")
    p.add_argument("--skip_weekends", action="store_true", help="skip Sat/Sun")
    p.add_argument("--resume", action="store_true", help="resume from progress file if present")
    p.add_argument("--max_days", type=int, default=0, help="0 = no limit; otherwise stop after N processed days")

    args = p.parse_args()

    d1 = date.fromisoformat(args.from_date)
    d2 = date.fromisoformat(args.to_date)

    # progress file in project root
    root = Path(__file__).resolve().parents[2]
    prog_path = root / f".backfill_progress_{args.symbol}_{args.callput}_{args.dte}.json"

    state = load_progress(prog_path)
    last_done = state.get("last_done") if args.resume else None

    ok = 0
    fail = 0
    skipped = 0
    processed = 0

    for d in daterange(d1, d2):
        ds = d.isoformat()

        if args.skip_weekends and is_weekend(d):
            skipped += 1
            continue

        if last_done and ds <= last_done:
            # already done in previous run
            skipped += 1
            continue

        cmd = [
            sys.executable,
            r".\src\jobs\load_options_from_nearest_to_db.py",
            "--symbol", args.symbol,
            "--date", ds,
            "--dte", str(args.dte),
            "--callput", args.callput,
        ]
        if args.delta is not None:
            cmd += ["--delta", str(args.delta)]
        else:
            cmd += ["--moneyness", str(args.moneyness)]

        print(f"\n===== {ds} =====")
        rc = subprocess.call(cmd, shell=False)
        processed += 1

        if rc == 0:
            ok += 1
            state["last_done"] = ds
            state["ok"] = ok
            state["fail"] = fail
            state["skipped"] = skipped
            save_progress(prog_path, state)
        else:
            fail += 1
            state["fail"] = fail
            save_progress(prog_path, state)
            print(f"FAILED day {ds} (exit={rc})")

        if args.sleep and args.sleep > 0:
            time.sleep(args.sleep)

        if args.max_days and processed >= args.max_days:
            print(f"\nStopping early due to --max_days={args.max_days}")
            break

    print("\n=== BACKFILL SUMMARY ===")
    print("days ok:", ok)
    print("days failed:", fail)
    print("days skipped:", skipped)
    print("progress file:", prog_path)


if __name__ == "__main__":
    main()
