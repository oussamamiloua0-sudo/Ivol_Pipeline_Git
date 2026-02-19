$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$yesterday = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')

$pass = $true

try {
  .\.venv\Scripts\python.exe .\scripts\db_ssl_ping.py | Out-Null
} catch {
  $pass = $false
}

try {
  $q = @"
SELECT COUNT(*) AS n FROM `fact_underlying_eod` WHERE trade_date='$yesterday';
SELECT COUNT(*) AS n FROM `fact_vol_metrics` WHERE trade_date='$yesterday';
SELECT COUNT(*) AS n FROM `fact_option_eod` WHERE trade_date='$yesterday';
"@

  $mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
  & $mysql -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com -P 25060 -u ivol_app -p --ssl-mode=REQUIRED ivol -e $q | Out-Null
} catch {
  $pass = $false
}

if ($pass) {
  Write-Output "PASS"
} else {
  Write-Output "FAIL"
}
