$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$yesterday = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')

$pass = $true

function Get-EnvValue([string]$Key) {
  $envFile = Join-Path $root ".env"
  if (!(Test-Path $envFile)) { return $null }
  $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -First 1
  if (-not $line) { return $null }
  $value = ($line -split "=", 2)[1].Trim()
  if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
    $value = $value.Substring(1, $value.Length - 2)
  }
  return $value
}

function Get-MysqlPassword {
  $dbPassword = Get-EnvValue "DB_PASSWORD"
  if ($dbPassword) { return $dbPassword }

  $dbUrl = Get-EnvValue "DB_URL"
  if (-not $dbUrl) { return $null }
  try {
    $uri = [System.Uri]$dbUrl
    $userInfo = $uri.UserInfo
    if (-not $userInfo -or $userInfo.IndexOf(":") -lt 0) { return $null }
    $pwd = $userInfo.Substring($userInfo.IndexOf(":") + 1)
    if (-not $pwd) { return $null }
    return [System.Uri]::UnescapeDataString($pwd)
  } catch {
    return $null
  }
}

try {
  .\.venv\Scripts\python.exe .\scripts\db_ssl_ping.py | Out-Null
  if ($LASTEXITCODE -ne 0) { $pass = $false }
} catch {
  $pass = $false
}

try {
  $q = @'
SELECT COUNT(*) AS n FROM `fact_underlying_eod` WHERE trade_date='{0}';
SELECT COUNT(*) AS n FROM `fact_vol_metrics` WHERE trade_date='{0}';
SELECT COUNT(*) AS n FROM `fact_option_eod` WHERE trade_date='{0}';
'@ -f $yesterday

  $mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
  $mysqlPwd = Get-MysqlPassword
  if ($mysqlPwd) { $env:MYSQL_PWD = $mysqlPwd }
  & $mysql -h db-mysql-nyc3-06366-do-user-33453591-0.e.db.ondigitalocean.com -P 25060 -u ivol_app --ssl-mode=REQUIRED ivol -e $q | Out-Null
  if ($LASTEXITCODE -ne 0) { $pass = $false }
  if (Test-Path Env:\MYSQL_PWD) { Remove-Item Env:\MYSQL_PWD }
} catch {
  if (Test-Path Env:\MYSQL_PWD) { Remove-Item Env:\MYSQL_PWD }
  $pass = $false
}

if ($pass) {
  Write-Output "PASS"
} else {
  Write-Output "FAIL"
}
