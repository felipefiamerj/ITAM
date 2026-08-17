param(
  [string]$ListenHost = '127.0.0.1',
  [int]$Port = 8000,
  [ValidateRange(30, 600)]
  [int]$DockerTimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

$docker = Get-Command docker.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1

function Test-DockerReady {
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $docker.Source
  $startInfo.Arguments = 'info --format "{{.ServerVersion}}"'
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $startInfo

  try {
    if (-not $process.Start()) {
      return $false
    }
    if (-not $process.WaitForExit(5000)) {
      $process.Kill()
      $process.WaitForExit()
      return $false
    }
    return $process.ExitCode -eq 0
  } catch {
    return $false
  } finally {
    $process.Dispose()
  }
}

if (-not (Test-DockerReady)) {
  $dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
  if (-not (Test-Path -LiteralPath $dockerDesktop -PathType Leaf)) {
    throw "Docker Desktop nao encontrado: $dockerDesktop"
  }
  if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null
  }

  $dockerReady = $false
  $dockerDeadline = [DateTime]::UtcNow.AddSeconds($DockerTimeoutSeconds)
  do {
    Start-Sleep -Seconds 2
    if (Test-DockerReady) {
      $dockerReady = $true
      break
    }
  } while ([DateTime]::UtcNow -lt $dockerDeadline)
  if (-not $dockerReady) {
    throw "Docker Desktop nao ficou pronto em $DockerTimeoutSeconds segundos."
  }
}

& $docker.Source compose -f (Join-Path $repoRoot 'compose.redis.yml') up -d
if ($LASTEXITCODE -ne 0) {
  throw "Redis nao iniciou pelo Docker Compose. Codigo: $LASTEXITCODE"
}

$redisHealthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
  Start-Sleep -Seconds 1
  $redisHealth = & $docker.Source inspect --format '{{.State.Health.Status}}' itam-redis 2>$null
  if ($LASTEXITCODE -eq 0 -and $redisHealth -eq 'healthy') {
    $redisHealthy = $true
    break
  }
}
if (-not $redisHealthy) {
  throw "Redis nao ficou saudavel. Estado: $redisHealth"
}

& (Join-Path $PSScriptRoot 'start-all.ps1') -ListenHost $ListenHost -Port $Port

$healthHost = if ($ListenHost -eq '0.0.0.0') { '127.0.0.1' } else { $ListenHost }
$healthUrl = "http://$healthHost`:$Port/health/"
$applicationReady = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
  Start-Sleep -Seconds 1
  try {
    $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
    if ($response.StatusCode -eq 200) {
      $applicationReady = $true
      break
    }
  } catch {
    continue
  }
}
if (-not $applicationReady) {
  throw "Aplicacao nao ficou saudavel em $healthUrl"
}

$agentConfig = Join-Path $repoRoot 'agents\windows\itam-agent.config.json'
$agentLoop = Join-Path $PSScriptRoot 'start-agent-loop.ps1'
if (Test-Path -LiteralPath $agentConfig -PathType Leaf) {
  $logDir = Join-Path $repoRoot 'logs'
  $pidDir = Join-Path $logDir 'pids'
  New-Item -ItemType Directory -Force -Path $pidDir | Out-Null
  $agentPidFile = Join-Path $pidDir 'agent.pid'
  $agentRunning = $false

  if (Test-Path -LiteralPath $agentPidFile -PathType Leaf) {
    $rawPid = Get-Content -LiteralPath $agentPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $agentProcess = if ($rawPid -as [int]) {
      Get-CimInstance Win32_Process -Filter "ProcessId = $rawPid" -ErrorAction SilentlyContinue
    }
    $agentRunning = $agentProcess -and $agentProcess.CommandLine -like '*start-agent-loop.ps1*'
    if (-not $agentRunning) {
      Remove-Item -LiteralPath $agentPidFile -Force
    }
  }

  if (-not $agentRunning) {
    $agentArgs = @(
      '-NoProfile',
      '-NonInteractive',
      '-ExecutionPolicy', 'Bypass',
      '-File', "`"$agentLoop`"",
      '-ConfigPath', "`"$agentConfig`"",
      '-IntervalSeconds', '300'
    )
    $agentProcess = Start-Process `
      -WindowStyle Hidden `
      -FilePath 'powershell.exe' `
      -ArgumentList $agentArgs `
      -WorkingDirectory $repoRoot `
      -RedirectStandardOutput (Join-Path $logDir 'agent.out.log') `
      -RedirectStandardError (Join-Path $logDir 'agent.err.log') `
      -PassThru
    Set-Content -LiteralPath $agentPidFile -Value $agentProcess.Id
    Write-Host "Agente de monitoramento iniciado com PID $($agentProcess.Id)."
  } else {
    Write-Host "Agente de monitoramento ja esta em execucao com PID $rawPid."
  }
} else {
  Write-Warning "Configuracao do agente nao encontrada: $agentConfig"
}

Write-Host "Runtime ITAM pronto em http://$ListenHost`:$Port"
