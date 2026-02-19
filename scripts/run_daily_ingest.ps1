param(
  [string]$Symbols = 'TQQQ',
  [ValidateSet('C','P')][string]$CallPut = 'P',
  [int]$Dte = 100,
  [string]$Deltas = '-0.17'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (!(Test-Path .\logs)) { New-Item -ItemType Directory -Force -Path .\logs | Out-Null }

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path .\logs "daily_ingest_${ts}.log"

$runDate = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')

. .\.venv\Scripts\Activate.ps1

$cmd = @(
  '.\.venv\Scripts\python.exe',
  '.\src\jobs\daily_ingest.py',
  '--symbols', $Symbols,
  '--callput', $CallPut,
  '--dte', $Dte.ToString(),
  '--deltas', $Deltas,
  '--date', $runDate
)

"Running daily ingest for date=$runDate symbols=$Symbols callput=$CallPut dte=$Dte deltas=$Deltas" | Tee-Object -FilePath $log -Append
& $cmd[0] $cmd[1..($cmd.Length-1)] 2>&1 | Tee-Object -FilePath $log -Append
