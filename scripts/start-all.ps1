param(
  [string]$PythonExe = '',
  [string]$Host = '0.0.0.0',
  [int]$Port = 8000
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
    (Join-Path $repoRoot '.venv310\Scripts\python.exe'),
    (Join-Path $repoRoot '.venv\Scripts\python.exe')
  )

  foreach ($candidate in $venvCandidates) {
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }
  }

  return 'python'
}

$python = Resolve-PythonExe -ProvidedPythonExe $PythonExe

$asgiArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'scripts\start-asgi.ps1'), '-PythonExe', $python, '-Host', $Host, '-Port', $Port)
$workerArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'scripts\start-worker.ps1'), '-PythonExe', $python)
$beatArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'scripts\start-beat.ps1'), '-PythonExe', $python)

Start-Process -WindowStyle Hidden -FilePath powershell.exe -ArgumentList $asgiArgs | Out-Null
Start-Process -WindowStyle Hidden -FilePath powershell.exe -ArgumentList $workerArgs | Out-Null
Start-Process -WindowStyle Hidden -FilePath powershell.exe -ArgumentList $beatArgs | Out-Null

Write-Host 'Servicos iniciados em segundo plano.'
Write-Host "ASGI em http://$Host`:$Port"
