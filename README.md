
# ivolatility-data-pipeline (Ivol_Pipeline_Git)

## Setup

1) Copy `.env.example` -> `.env`
2) Fill in `IVOL_API_KEY` and `DB_URL`

## Research View: `v_option_eod_research`

`v_option_eod_research` provides a research-ready, contract-level options view joined to the underlying symbol.

Column notes:
- `close_price` is mapped from `fact_option_eod.price` (vendor-provided close/mark field).
- `mid_price` is computed as `(bid + ask) / 2` when both sides are present.

## Apply Migration (PowerShell)

```powershell
Get-Content .\sql\002_options_research_view_and_indexes.sql |
  & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
    -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com `
    -P 25060 -u ivol_app -p --ssl-mode=REQUIRED ivol
```

## Verify

```sql
SHOW INDEX FROM `fact_option_eod`;
SHOW INDEX FROM `dim_option_contract`;
SHOW CREATE VIEW `v_option_eod_research`\G
SELECT * FROM `v_option_eod_research` LIMIT 5;
```

## Underlying & Volatility Tables

- `fact_underlying_eod`: daily underlying OHLCV and optional `adj_close` (may be NULL if not provided).
- `fact_vol_metrics`: daily IVX and historical volatility (HV) series with raw payloads stored in JSON.

Endpoints:
- `/equities/eod/stock-prices` ? `fact_underlying_eod` (param: `date`)
- `/equities/eod/ivx` ? `fact_vol_metrics.ivx` + `fact_vol_metrics.ivx_raw` (param: `date`)
- `/equities/eod/hv` ? `fact_vol_metrics.hv_*` + `fact_vol_metrics.hv_raw` (param: `date`)
  - If HV returns a single value, it is mapped to `hv_30`.

## Apply Migration (PowerShell)

```powershell
Get-Content .\sql\003_underlying_and_vol_tables.sql |
  & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
    -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com `
    -P 25060 -u ivol_app -p --ssl-mode=REQUIRED ivol
```

## Verify

```sql
SHOW CREATE TABLE `fact_underlying_eod`\G;
SHOW CREATE TABLE `fact_vol_metrics`\G;
SHOW INDEX FROM `fact_underlying_eod`;
SHOW INDEX FROM `fact_vol_metrics`;
```

## Joined Research View (Options + Underlying + Volatility)

`v_research_option_underlying_vol` joins options with underlying OHLCV and IVX/HV metrics on `underlying_id` + `trade_date`.
Underlying bid/ask may be NULL depending on the endpoint.

Example query:
```sql
SELECT trade_date, underlying_symbol, expiration_date, strike, option_type, ivx, hv30
FROM `v_research_option_underlying_vol`
WHERE underlying_symbol='TQQQ' AND trade_date='2022-03-15'
LIMIT 10;
```

TSV export (stable column order):
```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
  -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com `
  -P 25060 -u ivol_app -p --ssl-mode=REQUIRED --batch --raw ivol `
  -e "SELECT trade_date, underlying_symbol, expiration_date, strike, option_type, bid, ask, mid_price, close_price, volume, open_interest, iv, preiv, delta, gamma, theta, vega, rho, is_settlement, option_id, option_symbol, underlying_open, underlying_high, underlying_low, underlying_close, underlying_volume, ivx, hv10, hv20, hv30, hv60, hv90 FROM v_research_option_underlying_vol WHERE underlying_symbol='TQQQ' AND trade_date='2022-03-15';" |
  Set-Content -Encoding ascii .\research_export.tsv
```

## Apply Migration (PowerShell)

```powershell
Get-Content .\sql\004_joined_research_view.sql |
  & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
    -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com `
    -P 25060 -u ivol_app -p --ssl-mode=REQUIRED ivol
```

## Verify

```sql
SHOW CREATE VIEW `v_research_option_underlying_vol`\G;
SELECT * FROM `v_research_option_underlying_vol` WHERE underlying_symbol='TQQQ' AND trade_date='2022-03-15' LIMIT 5;
```

## Team Access (Read-only)

SQL (update the password before running):
```sql
CREATE USER IF NOT EXISTS 'sqilled_support'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
GRANT SELECT ON `ivol`.* TO 'sqilled_support'@'%';
FLUSH PRIVILEGES;
```

Apply (PowerShell):
```powershell
Get-Content .\sql\005_team_readonly_user.sql |
  & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" `
    -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com `
    -P 25060 -u ivol_app -p --ssl-mode=REQUIRED ivol
```

Verify:
```sql
SHOW GRANTS FOR 'sqilled_support'@'%';
SELECT COUNT(*) FROM `v_research_option_underlying_vol`;
```

## Daily Ops / Scheduling

Manual run (PowerShell):
```powershell
.\scripts\run_daily_ingest.ps1 -Symbols TQQQ -CallPut P -Dte 100 -Deltas -0.17
```

Task Scheduler:
- Program/script: `powershell.exe`
- Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\Users\Acer\PycharmProjects\ivolatility-data-pipeline\scripts\run_daily_ingest.ps1"`
- Start in: `C:\Users\Acer\PycharmProjects\ivolatility-data-pipeline`

Troubleshooting:
- If DB connections fail, check DigitalOcean Trusted Sources for your current public IP (it may change).
