param(
  [string]$TaskName = 'ITAM Daily Backup',
  [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
  [string]$At = '19:00',
  [ValidateRange(1, 3650)]
  [int]$RetentionDays = 30,
  [string]$OutputDir = '',
  [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backupScript = Join-Path $PSScriptRoot 'backup.ps1'

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

if (-not (Test-Path -LiteralPath $backupScript -PathType Leaf)) {
  throw "Script de backup nao encontrado: $backupScript"
}

$powershellCommand = Get-Command powershell.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
$powershellExe = $powershellCommand.Source
$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$backupScript`" -RetentionDays $RetentionDays"
if ($OutputDir) {
  $resolvedOutputDir = (New-Item -ItemType Directory -Force -Path $OutputDir).FullName
  $arguments += " -OutputDir `"$resolvedOutputDir`""
}

$scheduleTime = [DateTime]::Today.Add([TimeSpan]::ParseExact($At, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $scheduleTime
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description 'Backup diario do banco e da pasta media do ITAM.' `
  -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Tarefa instalada: $($task.TaskName)"
Write-Host "Proxima execucao: $($taskInfo.NextRunTime)"
Write-Host "Retencao local: $RetentionDays dia(s)"
