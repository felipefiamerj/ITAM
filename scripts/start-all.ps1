param(
  [string]$PythonExe = '',
  [string]$ListenHost = '0.0.0.0',
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

$asgiArgs = @('-m', 'daphne', 'itam.asgi:application', '-b', $ListenHost, '-p', "$Port")
$workerArgs = @('-m', 'celery', '-A', 'itam', 'worker', '-l', 'info')
if ($env:OS -eq 'Windows_NT') {
  $workerArgs += @('--pool', 'solo')
}
$beatArgs = @('-m', 'celery', '-A', 'itam', 'beat', '-l', 'info')

$services = @(
  @{ Name = 'asgi'; Args = $asgiArgs; Signature = 'daphne itam.asgi:application' },
  @{ Name = 'worker'; Args = $workerArgs; Signature = 'celery -A itam worker' },
  @{ Name = 'beat'; Args = $beatArgs; Signature = 'celery -A itam beat' }
)

foreach ($service in $services) {
  $pidFile = Join-Path $pidDir "$($service.Name).pid"
  if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
    $rawPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $existingProcess = if ($rawPid -as [int]) {
      Get-CimInstance Win32_Process -Filter "ProcessId = $rawPid" -ErrorAction SilentlyContinue
    }
    if ($existingProcess -and $existingProcess.CommandLine -like "*$($service.Signature)*") {
      Write-Host "$($service.Name) ja esta em execucao com PID $rawPid."
      continue
    }
    Remove-Item -LiteralPath $pidFile -Force
  }

  $stdout = Join-Path $logDir "$($service.Name).out.log"
  $stderr = Join-Path $logDir "$($service.Name).err.log"
  $process = Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList $service.Args -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
  Set-Content -LiteralPath $pidFile -Value $process.Id
  Write-Host "$($service.Name) iniciado com PID $($process.Id). Logs: $stdout / $stderr"
}

Write-Host 'Servicos iniciados em segundo plano.'
Write-Host "ASGI em http://$ListenHost`:$Port"
