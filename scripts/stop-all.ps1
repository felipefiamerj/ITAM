param(
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidDir = Join-Path $repoRoot 'logs\pids'

if (-not (Test-Path $pidDir)) {
  Write-Host "Nenhum diretorio de PIDs encontrado: $pidDir"
  return
}

$signatures = @{
  asgi = 'daphne itam.asgi:application'
  worker = 'celery -A itam worker'
  beat = 'celery -A itam beat'
  agent = 'start-agent-loop.ps1'
}

Get-ChildItem -LiteralPath $pidDir -File -Filter '*.pid' | ForEach-Object {
  $rawPid = (Get-Content -LiteralPath $_.FullName -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $rawPid) {
    Remove-Item -LiteralPath $_.FullName -Force
    return
  }

  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $rawPid" -ErrorAction SilentlyContinue
  if ($process) {
    $signature = $signatures[$_.BaseName]
    if (-not $signature -or $process.CommandLine -notlike "*$signature*") {
      Write-Warning "PID $rawPid nao corresponde ao servico $($_.BaseName). O processo nao sera encerrado."
      Remove-Item -LiteralPath $_.FullName -Force
      return
    }
    Write-Host "Parando $($_.BaseName) PID $rawPid"
    Stop-Process -Id $process.ProcessId -Force:$Force
  } else {
    Write-Host "PID $rawPid de $($_.BaseName) nao esta em execucao."
  }
  Remove-Item -LiteralPath $_.FullName -Force
}

Write-Host 'Servicos sinalizados para parada.'
