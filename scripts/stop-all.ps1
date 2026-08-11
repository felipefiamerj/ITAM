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

Get-ChildItem -LiteralPath $pidDir -File -Filter '*.pid' | ForEach-Object {
  $rawPid = (Get-Content -LiteralPath $_.FullName -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $rawPid) {
    Remove-Item -LiteralPath $_.FullName -Force
    return
  }

  $process = Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue
  if ($process) {
    Write-Host "Parando $($_.BaseName) PID $rawPid"
    Stop-Process -Id $process.Id -Force:$Force
  } else {
    Write-Host "PID $rawPid de $($_.BaseName) nao esta em execucao."
  }
  Remove-Item -LiteralPath $_.FullName -Force
}

Write-Host 'Servicos sinalizados para parada.'
