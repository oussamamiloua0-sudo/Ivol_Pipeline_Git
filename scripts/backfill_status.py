"""
Backfill status checker — posts a clean summary to Slack.
Run on the droplet: python /opt/pipeline/backfill_status.py
"""
import os, json, re, glob, subprocess, urllib.request
from datetime import datetime, timedelta

WEBHOOK = "https://hooks.slack.com/services/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"
LOG_DIR = "/opt/pipeline/logs"
PIPELINE_DIR = "/opt/pipeline"


def post_slack(msg):
    data = json.dumps({"text": msg}).encode()
    req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    print(msg)


# Find most recent chain or backfill log
logs = sorted(
    glob.glob(f"{LOG_DIR}/chain_*.log") + glob.glob(f"{LOG_DIR}/backfill_SPY_*.log"),
    key=os.path.getmtime, reverse=True
)
if not logs:
    post_slack(":x: No backfill logs found -- droplet idle")
    raise SystemExit()

log_file = logs[0]

with open(log_file) as f:
    lines = f.readlines()

# Check if anything is running
screen = subprocess.run(["screen", "-ls"], capture_output=True, text=True).stdout
running = bool(re.search(r"chain_|spy_|bf_", screen))

if not running and any("ALL DONE" in l or "BACKFILL COMPLETE" in l for l in lines[-10:]):
    post_slack(":white_check_mark: No active backfill -- chain is COMPLETE")
    raise SystemExit()

# Parse current date and symbol from log
current_date = None
symbol = None
for line in reversed(lines):
    if not current_date:
        m = re.search(r"\[(\d{4}-\d{2}-\d{2})\] spot_close", line)
        if m:
            current_date = m.group(1)
    if not symbol:
        m = re.search(r"symbol\s+: (\w+)", line)
        if m:
            symbol = m.group(1)
    if current_date and symbol:
        break

# Parse progress file path from log
start_date = end_date = None
for line in lines:
    m = re.search(r"Progress file: \.backfill_(\w+)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.json", line)
    if m:
        symbol = m.group(1)
        start_date = m.group(2)
        end_date = m.group(3)

if not current_date:
    post_slack(":hourglass_flowing_sand: Backfill starting up -- no date yet")
    raise SystemExit()

# Count done days from checkpoint
done_days = 0
total_days = 0
if start_date and end_date and symbol:
    cp_file = f"{PIPELINE_DIR}/.backfill_{symbol}_{start_date}_{end_date}.json"
    if os.path.exists(cp_file):
        with open(cp_file) as f:
            cp = json.load(f)
        done_days = cp.get("days_done", 0)
    # Estimate total trading days (business days * 0.97 to account for holidays)
    s = datetime.strptime(start_date, "%Y-%m-%d").date()
    e = datetime.strptime(end_date, "%Y-%m-%d").date()
    total_days = int(sum(1 for n in range((e - s).days + 1)
                        if (s + timedelta(n)).weekday() < 5) * 0.97)

remaining = max(0, total_days - done_days)
mins = remaining * 8
hrs = mins // 60
mins_r = mins % 60
time_str = f"{hrs}h {mins_r}m" if hrs else f"{mins_r}m"

parts = [
    f"*{symbol}*",
    f"At: `{current_date}`",
    f"{done_days} done, ~{remaining} left",
    f"~{time_str} remaining",
]
if start_date and end_date:
    parts.append(f"Range: {start_date} to {end_date}")

post_slack(" | ".join(parts))
