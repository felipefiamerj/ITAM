param(
  [Parameter(Mandatory = $true)]
  [string]$DatabaseBackup,
  [string]$MediaBackup = '',
  [Parameter(Mandatory = $true)]
  [string]$StatusFile,
  [ValidateRange(1, 30)]
  [int]$RetentionDays = 30,
  [Parameter(Mandatory = $true)]
  [string]$Times,
  [string]$InitiatedBy = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

function Write-RestoreStatus {
  param(
    [string]$Status,
    [string]$Stage,
    [string]$Message
  )

  $statusDirectory = Split-Path -Parent $StatusFile
  New-Item -ItemType Directory -Force -Path $statusDirectory | Out-Null
  $temporaryFile = "$StatusFile.tmp"
  [pscustomobject]@{
    status = $Status
    stage = $Stage
    message = $Message
    initiated_by = $InitiatedBy
    updated_at = (Get-Date).ToString('o')
  } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporaryFile -Encoding UTF8
  Move-Item -LiteralPath $temporaryFile -Destination $StatusFile -Force
}

function Assert-LastExitCode {
  param([string]$Description)
  if ($LASTEXITCODE -ne 0) {
    throw "$Description falhou. Codigo: $LASTEXITCODE"
  }
}

$mutex = New-Object System.Threading.Mutex($false, 'Local\ITAMRestorePoint')
$mutexAcquired = $false
$servicesStopped = $false
$restoreError = $null
$backupTaskWasEnabled = $false

try {
  $mutexAcquired = $mutex.WaitOne(0)
  if (-not $mutexAcquired) {
    throw 'Ja existe uma restauracao em andamento.'
  }

  $backupTask = Get-ScheduledTask -TaskName 'ITAM Daily Backup' -ErrorAction SilentlyContinue
  if ($backupTask -and $backupTask.State -eq 'Running') {
    throw 'Aguarde o backup em andamento terminar antes de restaurar.'
  }
  if ($backupTask -and $backupTask.Settings.Enabled) {
    $backupTaskWasEnabled = $true
    Disable-ScheduledTask -TaskName 'ITAM Daily Backup' | Out-Null
  }

  Write-RestoreStatus -Status 'running' -Stage 'safety_backup' -Message 'Criando copia de seguranca do estado atual.'
  & (Join-Path $PSScriptRoot 'backup.ps1') -RetentionDays 30

  Write-RestoreStatus -Status 'running' -Stage 'stopping' -Message 'Parando os servicos para restaurar com consistencia.'
  $servicesStopped = $true
  & (Join-Path $PSScriptRoot 'stop-all.ps1') -Force

  Write-RestoreStatus -Status 'running' -Stage 'restoring' -Message 'Restaurando banco de dados e arquivos.'
  $restoreArguments = @(
    '-DatabaseBackup', $DatabaseBackup,
    '-ConfirmRestore', 'RESTORE'
  )
  if ($MediaBackup) {
    $restoreArguments += @('-MediaBackup', $MediaBackup, '-ReplaceMedia')
  }
  & (Join-Path $PSScriptRoot 'restore.ps1') @restoreArguments

  $python = Join-Path $repoRoot '.venv312\Scripts\python.exe'
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = 'python'
  }

  Write-RestoreStatus -Status 'running' -Stage 'migrating' -Message 'Atualizando a estrutura e os arquivos derivados.'
  & $python manage.py migrate --noinput
  Assert-LastExitCode -Description 'Migracao do banco'

  $env:ITAM_BACKUP_RETENTION = [string]$RetentionDays
  $env:ITAM_BACKUP_TIMES = $Times
  & $python manage.py shell -c "import os; from dashboard.models import BackupConfiguration; BackupConfiguration.objects.update_or_create(pk=1, defaults={'retention_days': int(os.environ['ITAM_BACKUP_RETENTION']), 'schedule_times': os.environ['ITAM_BACKUP_TIMES'].split(',')})"
  Assert-LastExitCode -Description 'Preservacao da configuracao de backup'

  & $python manage.py regenerar_qrcodes --force
  Assert-LastExitCode -Description 'Regeneracao dos QR Codes'
  & $python manage.py shell -c 'from django.core.cache import cache; cache.clear()'
  Assert-LastExitCode -Description 'Limpeza do cache'
} catch {
  $restoreError = $_.Exception.Message
} finally {
  if ($backupTaskWasEnabled) {
    try {
      Enable-ScheduledTask -TaskName 'ITAM Daily Backup' | Out-Null
    } catch {
      $taskError = $_.Exception.Message
      $restoreError = if ($restoreError) { "$restoreError Agendamento: $taskError" } else { "Agendamento: $taskError" }
    }
  }

  if ($servicesStopped) {
    try {
      Write-RestoreStatus -Status 'running' -Stage 'starting' -Message 'Reiniciando e verificando o sistema.'
      & (Join-Path $PSScriptRoot 'start-runtime.ps1') -ListenHost '127.0.0.1' -Port 8000
    } catch {
      $startupError = $_.Exception.Message
      $restoreError = if ($restoreError) { "$restoreError Reinicio: $startupError" } else { "Reinicio: $startupError" }
    }
  }

  if ($restoreError) {
    Write-RestoreStatus -Status 'failed' -Stage 'finished' -Message $restoreError
  } else {
    Write-RestoreStatus -Status 'completed' -Stage 'finished' -Message 'Ponto de restauracao aplicado com sucesso.'
  }

  if ($mutexAcquired) {
    $mutex.ReleaseMutex()
  }
  $mutex.Dispose()
}

if ($restoreError) {
  throw $restoreError
}
