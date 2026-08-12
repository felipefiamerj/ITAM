param(
  [string]$TaskName = 'ITAM Daily Backup',
  [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
  [string]$At = '19:00',
  [string]$Times = '',
  [ValidateRange(1, 30)]
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

$requestedTimes = if ($Times) { @($Times.Split(',')) } else { @($At) }
$scheduleTimes = @()
foreach ($requestedTime in $requestedTimes) {
  $normalizedTime = $requestedTime.Trim()
  try {
    $parsedTime = [DateTime]::ParseExact($normalizedTime, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
  } catch {
    throw "Horario de backup invalido: $normalizedTime. Use HH:mm."
  }
  $canonicalTime = $parsedTime.ToString('HH:mm')
  if ($scheduleTimes -notcontains $canonicalTime) {
    $scheduleTimes += $canonicalTime
  }
}
if ($scheduleTimes.Count -eq 0) {
  throw 'Informe pelo menos um horario para o backup.'
}
$scheduleTimes = @($scheduleTimes | Sort-Object)

$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments -WorkingDirectory $repoRoot
$triggers = @(
  foreach ($scheduleTime in $scheduleTimes) {
    $time = [DateTime]::Today.Add([DateTime]::ParseExact($scheduleTime, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture).TimeOfDay)
    New-ScheduledTaskTrigger -Daily -At $time
  }
)
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
  -Trigger $triggers `
  -Settings $settings `
  -Principal $principal `
  -Description 'Backup diario do banco e da pasta media do ITAM.' `
  -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Tarefa instalada: $($task.TaskName)"
Write-Host "Proxima execucao: $($taskInfo.NextRunTime)"
Write-Host "Retencao local: $RetentionDays dia(s)"
Write-Host "Horarios diarios: $($scheduleTimes -join ', ')"
