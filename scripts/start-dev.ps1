param(
  [string]$ListenHost = '0.0.0.0',
  [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv312\Scripts\python.exe'

if (-not (Test-Path $python)) {
  throw 'Ambiente .venv312 nao encontrado. Rode: py -3.12 -m venv .venv312; .\.venv312\Scripts\python.exe -m pip install -r requirements-dev.txt'
}

Set-Location $repoRoot
& $python manage.py runserver "$ListenHost`:$Port"
