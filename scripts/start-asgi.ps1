param(
  [string]$PythonExe = '',
  [string]$Host = '0.0.0.0',
  [int]$Port = 8000
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

  return 'python'
}

$python = Get-PythonExe -ProvidedPythonExe $PythonExe
& $python -m daphne itam.asgi:application -b $Host -p $Port
