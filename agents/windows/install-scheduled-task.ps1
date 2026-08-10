param(
  [Parameter(Mandatory = $true)]
  [string]$BaseUrl,
  [Parameter(Mandatory = $true)]
  [string]$AgentToken,
  [string]$AssetId = '',
  [string]$ServiceTag = '',
  [string]$SerialNumber = '',
  [string]$TaskName = 'ITAM Monitoring Agent',
  [int]$IntervalMinutes = 5,
  [string]$InstallDir = "$env:ProgramData\ITAM\agent",
  [switch]$RunNow,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

if (-not $Force -and (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
  throw "A tarefa $TaskName ja existe. Use -Force para substituir."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$sourceAgent = Join-Path $PSScriptRoot 'itam-agent.ps1'
if (-not (Test-Path $sourceAgent)) {
  throw "Agente nao encontrado: $sourceAgent"
}

$agentPath = Join-Path $InstallDir 'itam-agent.ps1'
$configPath = Join-Path $InstallDir 'itam-agent.config.json'
Copy-Item -LiteralPath $sourceAgent -Destination $agentPath -Force

$config = @{
  BaseUrl = $BaseUrl.TrimEnd('/')
  AgentToken = $AgentToken
  AssetId = $AssetId
  ServiceTag = $ServiceTag
  SerialNumber = $SerialNumber
  TimeoutSec = 30
  BatteryWarningPercent = 25
  BatteryCriticalPercent = 15
  DiskWarningFreePercent = 10
  DiskCriticalFreePercent = 5
}

$config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8

if ($Force -and (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
  -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$agentPath`" -ConfigPath `"$configPath`""

$trigger = New-ScheduledTaskTrigger `
  -Once `
  -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
  -RepetitionDuration (New-TimeSpan -Days 3650)

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null

if ($RunNow) {
  Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Agente instalado em $InstallDir"
Write-Host "Tarefa agendada: $TaskName a cada $IntervalMinutes minuto(s)"
Write-Host "Config: $configPath"
