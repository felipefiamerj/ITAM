param(
  [string]$OutputDir = '',
  [string]$PythonExe = '',
  [switch]$SkipDatabase,
  [switch]$SkipMedia
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

function Read-DotEnv {
  param([string]$Path)

  $values = @{}
  if (-not (Test-Path $Path)) {
    return $values
  }

  foreach ($rawLine in Get-Content -LiteralPath $Path) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) {
      continue
    }
    if ($line.StartsWith('export ')) {
      $line = $line.Substring(7)
    }
    $parts = $line.Split('=', 2)
    $key = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"').Trim("'")
    $values[$key] = $value
  }
  return $values
}

function Get-Setting {
  param(
    [hashtable]$EnvValues,
    [string]$Name,
    [string]$Default = ''
  )

  $envValue = [Environment]::GetEnvironmentVariable($Name)
  if ($envValue) {
    return $envValue
  }
  if ($EnvValues.ContainsKey($Name)) {
    return $EnvValues[$Name]
  }
  return $Default
}

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

$envValues = Read-DotEnv -Path (Join-Path $repoRoot '.env')
$backupRoot = if ($OutputDir) { $OutputDir } else { Get-Setting -EnvValues $envValues -Name 'BACKUP_DIR' -Default (Join-Path $repoRoot 'backups') }
$backupRoot = (New-Item -ItemType Directory -Force -Path $backupRoot).FullName
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$created = @()

if (-not $SkipDatabase) {
  $databaseUrl = Get-Setting -EnvValues $envValues -Name 'DATABASE_URL'
  $dbEngine = Get-Setting -EnvValues $envValues -Name 'DB_ENGINE' -Default 'django.db.backends.sqlite3'

  if ($databaseUrl -and ($databaseUrl.StartsWith('postgres://') -or $databaseUrl.StartsWith('postgresql://'))) {
    $dumpFile = Join-Path $backupRoot "itam-db-$timestamp.dump"
    & pg_dump --format=custom --no-owner --no-acl --file $dumpFile $databaseUrl
    $created += $dumpFile
  } elseif ($dbEngine -eq 'django.db.backends.postgresql') {
    $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'itam'
    $dbUser = Get-Setting -EnvValues $envValues -Name 'DB_USER' -Default 'itam'
    $dbPassword = Get-Setting -EnvValues $envValues -Name 'DB_PASSWORD'
    $dbHost = Get-Setting -EnvValues $envValues -Name 'DB_HOST' -Default '127.0.0.1'
    $dbPort = Get-Setting -EnvValues $envValues -Name 'DB_PORT' -Default '5432'
    $dumpFile = Join-Path $backupRoot "itam-db-$timestamp.dump"

    $oldPgPassword = $env:PGPASSWORD
    try {
      if ($dbPassword) {
        $env:PGPASSWORD = $dbPassword
      }
      & pg_dump --format=custom --no-owner --no-acl --host $dbHost --port $dbPort --username $dbUser --file $dumpFile $dbName
    } finally {
      $env:PGPASSWORD = $oldPgPassword
    }
    $created += $dumpFile
  } elseif ($dbEngine -eq 'django.db.backends.sqlite3') {
    $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'db.sqlite3'
    $dbPath = if ([System.IO.Path]::IsPathRooted($dbName)) { $dbName } else { Join-Path $repoRoot $dbName }
    if (-not (Test-Path $dbPath)) {
      throw "Banco SQLite nao encontrado: $dbPath"
    }
    $sqliteFile = Join-Path $backupRoot "itam-db-$timestamp.sqlite3"
    Copy-Item -LiteralPath $dbPath -Destination $sqliteFile
    $created += $sqliteFile
  } else {
    Write-Warning "Backup automatico nao implementado para DB_ENGINE=$dbEngine. Use a ferramenta nativa do banco."
  }
}

if (-not $SkipMedia) {
  $mediaDir = Join-Path $repoRoot 'media'
  if (Test-Path $mediaDir) {
    $mediaItems = Get-ChildItem -LiteralPath $mediaDir -Force
    if ($mediaItems.Count -gt 0) {
      $mediaFile = Join-Path $backupRoot "itam-media-$timestamp.zip"
      Compress-Archive -Path (Join-Path $mediaDir '*') -DestinationPath $mediaFile -Force
      $created += $mediaFile
    } else {
      Write-Warning "Diretorio media esta vazio. Nenhum arquivo de media foi compactado."
    }
  } else {
    Write-Warning "Diretorio media nao encontrado: $mediaDir"
  }
}

$python = Resolve-PythonExe -ProvidedPythonExe $PythonExe
$manifestFile = Join-Path $backupRoot "itam-backup-$timestamp.manifest.txt"
@(
  "created_at=$(Get-Date -Format o)",
  "repo_root=$repoRoot",
  "python=$python",
  "files=$($created -join ';')"
) | Set-Content -LiteralPath $manifestFile -Encoding UTF8
$created += $manifestFile

Write-Host 'Backup concluido:'
foreach ($file in $created) {
  Write-Host "  $file"
}
