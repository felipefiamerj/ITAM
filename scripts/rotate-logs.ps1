param(
  [string]$LogDir = '',
  [int]$RetentionDays = 30,
  [int]$ArchiveAfterDays = 7,
  [int]$MaxSizeMB = 50
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$targetLogDir = if ($LogDir) { $LogDir } else { Join-Path $repoRoot 'logs' }

if (-not (Test-Path $targetLogDir)) {
  Write-Host "Diretorio de logs nao existe: $targetLogDir"
  return
}

$archiveDir = Join-Path $targetLogDir 'archive'
New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
$now = Get-Date

Get-ChildItem -LiteralPath $targetLogDir -File -Filter '*.log*' | ForEach-Object {
  $ageDays = ($now - $_.LastWriteTime).TotalDays
  $tooLarge = $_.Length -ge ($MaxSizeMB * 1MB)
  $shouldArchive = $ageDays -ge $ArchiveAfterDays -or $tooLarge

  if ($shouldArchive) {
    $stamp = $_.LastWriteTime.ToString('yyyyMMdd-HHmmss')
    $zipName = "$($_.BaseName)-$stamp.zip"
    $zipPath = Join-Path $archiveDir $zipName
    Compress-Archive -LiteralPath $_.FullName -DestinationPath $zipPath -Force
    Clear-Content -LiteralPath $_.FullName
    Write-Host "Arquivado: $($_.Name) -> $zipPath"
  }
}

Get-ChildItem -LiteralPath $archiveDir -File -Filter '*.zip' | Where-Object {
  ($now - $_.LastWriteTime).TotalDays -ge $RetentionDays
} | ForEach-Object {
  Remove-Item -LiteralPath $_.FullName -Force
  Write-Host "Removido por retencao: $($_.Name)"
}

Write-Host 'Rotacao de logs concluida.'
