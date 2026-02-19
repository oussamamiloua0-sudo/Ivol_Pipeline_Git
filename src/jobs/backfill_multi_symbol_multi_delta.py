import argparse
import subprocess
import sys
import json
from pathlib import Path
from datetime import date, timedelta
import time


def daterange(d1: date, d2: date):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", required=True, help="comma-separated, e.g. TQQQ,AAPL,SPY")
    p.add_argument("--from_date", required=True)
    p.add_argument("--to_date", required=True)
    p.add_argument("--dte", required=True, type=int)
    p.add_argument("--callput", required=True, choices=["C", "P"])
    p.add_argument("--deltas", required=True, help="comma-separated, e.g. -0.10,-0.17,-0.30")
    p.add_argument("--sleep_day", type=float, default=0.2, help="sleep seconds between deltas")
    p.add_argument("--sleep_symbol", type=float, default=0.5, help="sleep seconds between symbols")
    p.add_argument("--skip_weekends", action="store_true")
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    deltas = [float(x.strip()) for x in args.deltas.split(",") if x.strip()]
    d1 = date.fromisoformat(args.from_date)
    d2 = date.fromisoformat(args.to_date)

    root = Path(__file__).resolve().parents[2]
    prog = root / f".progress_multi_symbol_{args.callput}_{args.dte}.json"
    state = load_state(prog) if args.resume else {}

    total_ok = 0
    total_fail = 0

    for sym in symbols:
        last_done = state.get(sym, {}).get("last_done") if args.resume else None
        sym_ok = 0
        sym_fail = 0
        sym_skipped = 0

        print(f"\n==============================")
        print(f"SYMBOL: {sym}")
        print(f"last_done (resume): {last_done}")
        print(f"==============================")

        for d in daterange(d1, d2):
            if args.skip_weekends and is_weekend(d):
                sym_skipped += 1
                continue

            ds = d.isoformat()
            if last_done and ds <= last_done:
                sym_skipped += 1
                continue

            print(f"\n----- {sym} {ds} -----")
            day_failed = False

            for de in deltas:
                cmd = [
                    sys.executable,
                    r".\src\jobs\load_options_from_nearest_to_db.py",
                    "--symbol", sym,
                    "--date", ds,
                    "--dte", str(args.dte),
                    "--callput", args.callput,
                    "--delta", str(de),
                ]
                print(f"delta {de} ...")
                rc = subprocess.call(cmd, shell=False)
                if rc != 0:
                    day_failed = True
                    print(f"FAILED delta {de} on {sym} {ds} (exit={rc})")

                if args.sleep_day and args.sleep_day > 0:
                    time.sleep(args.sleep_day)

            if day_failed:
                sym_fail += 1
                total_fail += 1
            else:
                sym_ok += 1
                total_ok += 1
                # update progress
                state.setdefault(sym, {})
                state[sym]["last_done"] = ds
                state[sym]["ok_days"] = sym_ok
                state[sym]["fail_days"] = sym_fail
                state[sym]["skipped_days"] = sym_skipped
                save_state(prog, state)

        print(f"\n=== SYMBOL SUMMARY {sym} ===")
        print("days ok:", sym_ok)
        print("days failed:", sym_fail)
        print("days skipped:", sym_skipped)

        if args.sleep_symbol and args.sleep_symbol > 0:
            time.sleep(args.sleep_symbol)

    print("\n=== TOTAL SUMMARY ===")
    print("symbols:", symbols)
    print("days ok:", total_ok)
    print("days failed:", total_fail)
    print("progress file:", prog)


if __name__ == "__main__":
    main()
