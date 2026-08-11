param(
  [string]$TaskName = 'ITAM Runtime',
  [string]$ListenHost = '127.0.0.1',
  [int]$Port = 8000,
  [ValidateRange(0, 600)]
  [int]$DelaySeconds = 30,
  [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeScript = Join-Path $PSScriptRoot 'start-runtime.ps1'

if ($Remove) {
  $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existingTask) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarefa removida: $TaskName"
  } else {
    Write-Host "Tarefa nao encontrada: $TaskName"
  }
  return
}

if (-not (Test-Path -LiteralPath $runtimeScript -PathType Leaf)) {
  throw "Script de runtime nao encontrado: $runtimeScript"
}

$powershellCommand = Get-Command powershell.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runtimeScript`" -ListenHost $ListenHost -Port $Port"
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershellCommand.Source -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
if ($DelaySeconds -gt 0) {
  $trigger.Delay = "PT$($DelaySeconds)S"
}
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description 'Inicia Docker, Redis, ASGI e automacoes do ITAM no logon.' `
  -Force | Out-Null

$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Tarefa instalada: $TaskName"
Write-Host "Ultimo resultado: $($taskInfo.LastTaskResult)"
