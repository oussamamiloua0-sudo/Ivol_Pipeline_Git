# ivolatility-data-pipeline

![Backfill on Droplet](https://github.com/oussamamiloua0-sudo/Ivol_Pipeline_Git/actions/workflows/backfill_droplet.yml/badge.svg)
![Droplet Status/Stop](https://github.com/oussamamiloua0-sudo/Ivol_Pipeline_Git/actions/workflows/droplet_manage.yml/badge.svg)

Full-chain EOD equity options ingest pipeline using the iVolatility REST API.

## Purpose

Build a complete EOD option chain for a given underlying, date, and expiration/strike
filter — then store each contract's raw implied-volatility metrics in MySQL.

**Pipeline flow:**
```
GET /equities/eod/option-series-on-date     (discover all optionIds for C and P)
        |
        v  for each optionId
GET /equities/eod/single-stock-option-raw-iv
        |
        v
MySQL:  dim_option_contract  +  fact_option_eod
```

## Setup

```bash
cp .env.example .env
# fill in IVOL_API_KEY and DB_URL
```

Required `.env` keys:

| Key | Description |
|---|---|
| `IVOL_API_KEY` | iVolatility REST API key |
| `DB_URL` | SQLAlchemy MySQL URL (`mysql+pymysql://user:pass@host:port/db`) |
| `DB_SSL_CA` | Path to DO CA certificate (optional, recommended) |
| `IVOL_BASE_URL` | Override API base URL (optional; default: `https://restapi.ivolatility.com`) |

## Core Scripts

### Full-chain ingest for one date

```bash
python src/jobs/load_full_chain_for_date.py \
    --symbol SPX \
    --date 2022-02-16 \
    --expFrom 2022-10-21 \
    --expTo 2022-10-21 \
    --strikeFrom 200 \
    --strikeTo 750 \
    --region USA \
    --debug --head 10
```

| Arg | Required | Description |
|---|---|---|
| `--symbol` | yes | Underlying ticker (e.g. SPX, AAPL) |
| `--date` | yes | Trade date (YYYY-MM-DD) |
| `--expFrom` | yes | Expiration filter start (YYYY-MM-DD) |
| `--expTo` | yes | Expiration filter end (YYYY-MM-DD) |
| `--strikeFrom` | yes | Strike lower bound |
| `--strikeTo` | yes | Strike upper bound |
| `--region` | no | Default: USA |
| `--sleep-ms N` | no | Throttle between raw-iv calls in ms (default 0) |
| `--head N` | no | In debug mode, print first N contracts (default 3; 0 = all) |
| `--debug` | no | Verbose output + contract/row previews |

### AAPL example

```bash
python src/jobs/load_full_chain_for_date.py \
    --symbol AAPL \
    --date 2022-03-15 \
    --expFrom 2022-06-17 \
    --expTo 2022-06-17 \
    --strikeFrom 100 \
    --strikeTo 200 \
    --region USA \
    --debug --head 10
```

### Inspect a single contract (debug / spot-check)

```bash
# By OCC symbol
python scripts/rawiv_single_contract.py \
    --symbol "SPX   251219C04100000" \
    --from 2022-09-29 --to 2022-10-30 \
    --region USA --head 10

# By iVol optionId
python scripts/rawiv_single_contract.py \
    --optionId 116950315 \
    --from 2022-02-16 --to 2022-02-16 \
    --region USA --head 5

# Save full JSON response to file
python scripts/rawiv_single_contract.py \
    --symbol "SPX   251219C04100000" \
    --from 2022-09-29 --to 2022-10-30 \
    --out response.json
```

## DB Schema

| Table | Role |
|---|---|
| `dim_underlying` | One row per ticker (auto-created on first ingest) |
| `dim_option_contract` | One row per option contract (PK: `option_id`) |
| `fact_option_eod` | EOD metrics per contract per date (PK: `option_id`, `trade_date`) |

Schema migrations are in `sql/`.  Reference schema: `src/db/schema.sql`.

## DB Connectivity (DigitalOcean Managed MySQL)

The engine in `src/db/engine.py` enforces SSL and runs a TCP preflight check on startup.

If you see `TCP connect failed`: your current public IP is not in DO Trusted Sources.
Add it at: DigitalOcean > Databases > your cluster > Settings > Trusted Sources.

```bash
python scripts/db_ssl_ping.py   # quick connectivity check
```

## Project Structure

```
src/
  db/
    engine.py                       # SSL-aware SQLAlchemy engine (shared)
    init_db.py                      # Schema initialiser
    schema.sql                      # Reference schema
  jobs/
    load_full_chain_for_date.py     # Main ingest job
  ivol/
    __init__.py

scripts/
  rawiv_single_contract.py          # Single-contract raw-IV debug/spot-check
  db_ssl_ping.py                    # DB connectivity check
  health_check.ps1                  # Health check (PowerShell)
  show_env.py                       # Print resolved env vars (no secrets)
  test_full_chain_aapl.py           # AAPL endpoint smoke test
  recheck_full_chain_access.py      # Check accessible endpoints

sql/                                # Schema migrations (apply manually)
  002_options_research_view_and_indexes.sql
  003_underlying_and_vol_tables.sql
  004_joined_research_view.sql
  005_team_readonly_user.sql

archive/nearest_selection_legacy/   # Old nearest-selection pipeline (inactive)
```

## Using multiple API keys + tuning performance

### Why multiple keys?

Each iVol API key has its own per-key request budget. Adding legitimately
issued keys lets you distribute raw-iv fetches across keys in parallel
threads, reducing per-day wall time roughly proportionally to the number of
keys (the bottleneck is network I/O, not CPU).

| Setup | ~time for 650 contracts | effective RPS |
|---|---|---|
| 1 key @ 2 RPS | ~325 s (~5 min) | 2 |
| 5 keys @ 2 RPS each | ~65 s (~1 min) | 10 |

Rate limits are respected per-key via a token-bucket inside `KeyPool`.
Adaptive backoff automatically widens the per-key gap when HTTP 429
responses are detected.

### Configuration

In `.env`, set a comma-separated list:

```
IVOL_API_KEYS=key1,key2,key3,key4,key5
```

`IVOL_API_KEY` (single key) is still supported and is merged into the pool
automatically (deduplication is applied). Set one or the other or both.

### New CLI flags

Both `load_full_chain_for_date.py` and `backfill_full_chain.py` accept:

| Flag | Default | Description |
|---|---|---|
| `--max-workers N` | number of keys | Parallel raw-iv fetcher threads |
| `--per-key-rps F` | `2.0` | Max requests/second per key |

### Run a single date with all keys

```bash
python src/jobs/load_full_chain_for_date.py \
    --symbol AAPL --date 2022-01-03 \
    --expFrom 2022-01-03 --expTo 2022-07-01 \
    --strikeFrom 127 --strikeTo 237 \
    --region USA \
    --per-key-rps 2.0 --debug
```

Expected log output:
```
KeyPool ready: 5 key(s), per_key_rps=2.0  [***6X, ***xz, ***36, ***4n, ***A0]
[2022-01-03] Discovered 644 contracts (calls=322 puts=322)  keys=5 workers=5
[2022-01-03] Fetched 644 raw-iv results in 65.2s  avg=9.9 req/s
[2022-01-03] OK  loaded=644  elapsed=67.1s  rps=9.9
```

### Run the 1-year AAPL backfill (resumable)

```bash
python src/jobs/backfill_full_chain.py \
    --symbol AAPL --start 2022-01-01 --end 2022-12-31 \
    --max-dte 180 --strike-low 0.70 --strike-high 1.30 \
    --per-key-rps 2.0 --region USA
```

Progress is checkpointed to `.backfill_AAPL_2022-01-01_2022-12-31.json`.
If interrupted, re-run the same command and it will resume from the last
completed date.

### Verify no secret leakage

```bash
# Keys should appear only as ***XXXX (last 4 chars) in logs:
grep -i "apikey\|api_key\|key=" logs/backfill_*.log || echo "Clean"
```

### Verify DB rows

```sql
SELECT trade_date, COUNT(*) AS contracts
FROM fact_option_eod f
JOIN dim_option_contract c ON c.option_id = f.option_id
JOIN dim_underlying u ON u.underlying_id = c.underlying_id
WHERE u.symbol = 'AAPL'
GROUP BY trade_date
ORDER BY trade_date;
```

---

## Apply a SQL migration (PowerShell)

```powershell
Get-Content .\sql\002_options_research_view_and_indexes.sql |
  & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
    -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com `
    -P 25060 -u ivol_app -p --ssl-mode=REQUIRED ivol
```

## Team read-only access

```sql
CREATE USER IF NOT EXISTS 'sqilled_support'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
GRANT SELECT ON `ivol`.* TO 'sqilled_support'@'%';
FLUSH PRIVILEGES;
```

Troubleshooting: if DB connections fail, check DigitalOcean Trusted Sources for your
current public IP (dynamic IPs change on reconnect).
