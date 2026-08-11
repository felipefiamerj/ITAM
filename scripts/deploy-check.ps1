param(
  [string]$PythonExe = '',
  [string]$BaseUrl = '',
  [string]$ApiKey = '',
  [switch]$SkipStaticDryRun,
  [switch]$SkipSmoke
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

function Resolve-PythonExe {
  param([string]$ProvidedPythonExe)

  if ($ProvidedPythonExe -and (Test-Path $ProvidedPythonExe)) {
    return (Resolve-Path $ProvidedPythonExe).Path
  }

  $venvCandidates = @(
    (Join-Path $repoRoot '.venv312\Scripts\python.exe'),
    (Join-Path $repoRoot '.venv\Scripts\python.exe'),
    (Join-Path $repoRoot '.venv313\Scripts\python.exe')
  )

  foreach ($candidate in $venvCandidates) {
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }
  }

  return 'python'
}

function Invoke-Step {
  param(
    [string]$Title,
    [scriptblock]$Command
  )

  Write-Host ''
  Write-Host "== $Title =="
  & $Command
}

$python = Resolve-PythonExe -ProvidedPythonExe $PythonExe

Invoke-Step 'Django system check' {
  & $python manage.py check
}

Invoke-Step 'Django deploy check' {
  & $python manage.py check --deploy
}

Invoke-Step 'Ambiente operacional' {
  & $python manage.py verificar_instalacao
}

Invoke-Step 'Migracoes pendentes' {
  & $python manage.py migrate --check --noinput
}

if (-not $SkipStaticDryRun) {
  Invoke-Step 'Collectstatic dry-run' {
    & $python manage.py collectstatic --noinput --dry-run --clear
  }
}

if ($BaseUrl -and -not $SkipSmoke) {
  Invoke-Step 'Smoke test HTTP' {
    $smokeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'scripts\smoke-test.ps1'), '-BaseUrl', $BaseUrl)
    if ($ApiKey) {
      $smokeArgs += @('-ApiKey', $ApiKey)
    }
    & powershell.exe @smokeArgs
  }
}

Write-Host ''
Write-Host 'Deploy check concluido.'
