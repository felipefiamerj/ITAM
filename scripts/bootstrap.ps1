param(
  [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

function Get-PythonExe {
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

  $defaultVenv = Join-Path $repoRoot '.venv312'
  if (-not (Test-Path $defaultVenv)) {
    py -3.12 -m venv $defaultVenv
  }

  $createdPython = Join-Path $defaultVenv 'Scripts\python.exe'
  if (-not (Test-Path $createdPython)) {
    throw 'Nao foi possivel localizar o Python do virtualenv.'
  }

  return (Resolve-Path $createdPython).Path
}

$python = Get-PythonExe -ProvidedPythonExe $PythonExe

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt
& $python manage.py migrate --noinput
& $python manage.py collectstatic --noinput
& $python manage.py verificar_instalacao

Write-Host ''
Write-Host 'Instalacao concluida.'
Write-Host "Para iniciar o sistema use:"
Write-Host "  .\scripts\start-all.ps1"
