param(
  [string]$ListenHost = '127.0.0.1',
  [int]$Port = 8000,
  [ValidateRange(30, 600)]
  [int]$DockerTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

$docker = Get-Command docker.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
& $docker.Source info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  $dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
  if (-not (Test-Path -LiteralPath $dockerDesktop -PathType Leaf)) {
    throw "Docker Desktop nao encontrado: $dockerDesktop"
  }
  if (-not (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null
  }

  $dockerReady = $false
  $attempts = [math]::Ceiling($DockerTimeoutSeconds / 2)
  for ($attempt = 0; $attempt -lt $attempts; $attempt++) {
    Start-Sleep -Seconds 2
    & $docker.Source info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
      $dockerReady = $true
      break
    }
  }
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

$healthUrl = "http://$ListenHost`:$Port/health/"
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

Write-Host "Runtime ITAM pronto em http://$ListenHost`:$Port"
