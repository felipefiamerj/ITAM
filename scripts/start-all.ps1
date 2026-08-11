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

$python = Resolve-PythonExe -ProvidedPythonExe $PythonExe
$logDir = Join-Path $repoRoot 'logs'
$pidDir = Join-Path $logDir 'pids'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $pidDir | Out-Null

$asgiArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'scripts\start-asgi.ps1'), '-PythonExe', $python, '-Host', $Host, '-Port', $Port)
$workerArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'scripts\start-worker.ps1'), '-PythonExe', $python)
$beatArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'scripts\start-beat.ps1'), '-PythonExe', $python)

$services = @(
  @{ Name = 'asgi'; Args = $asgiArgs },
  @{ Name = 'worker'; Args = $workerArgs },
  @{ Name = 'beat'; Args = $beatArgs }
)

foreach ($service in $services) {
  $stdout = Join-Path $logDir "$($service.Name).out.log"
  $stderr = Join-Path $logDir "$($service.Name).err.log"
  $process = Start-Process -WindowStyle Hidden -FilePath powershell.exe -ArgumentList $service.Args -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
  Set-Content -LiteralPath (Join-Path $pidDir "$($service.Name).pid") -Value $process.Id
  Write-Host "$($service.Name) iniciado com PID $($process.Id). Logs: $stdout / $stderr"
}

Write-Host 'Servicos iniciados em segundo plano.'
Write-Host "ASGI em http://$Host`:$Port"
