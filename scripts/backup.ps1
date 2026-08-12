param(
  [string]$OutputDir = '',
  [string]$PythonExe = '',
  [ValidateRange(0, 30)]
  [int]$RetentionDays = 30,
  [switch]$SkipDatabase,
  [switch]$SkipMedia,
  [switch]$IncludeGeneratedQrCodes
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

function Resolve-PostgresTool {
  param(
    [hashtable]$EnvValues,
    [string]$ToolName
  )

  $executableName = "$ToolName.exe"
  $configuredBin = Get-Setting -EnvValues $EnvValues -Name 'POSTGRES_BIN'
  if ($configuredBin) {
    $configuredTool = Join-Path $configuredBin $executableName
    if (-not (Test-Path -LiteralPath $configuredTool -PathType Leaf)) {
      throw "Ferramenta PostgreSQL nao encontrada em POSTGRES_BIN: $configuredTool"
    }
    return (Resolve-Path -LiteralPath $configuredTool).Path
  }

  $command = Get-Command $ToolName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($command) {
    return $command.Source
  }

  $postgresRoots = @(
    (Join-Path $env:ProgramFiles 'PostgreSQL'),
    (Join-Path ${env:ProgramFiles(x86)} 'PostgreSQL')
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }

  foreach ($postgresRoot in $postgresRoots) {
    $versionDirs = Get-ChildItem -LiteralPath $postgresRoot -Directory -ErrorAction SilentlyContinue |
      Sort-Object @{ Expression = { [int]($_.Name -replace '\D.*$', '') }; Descending = $true }
    foreach ($versionDir in $versionDirs) {
      $candidate = Join-Path $versionDir.FullName "bin\$executableName"
      if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        return $candidate
      }
    }
  }

  throw "Ferramenta PostgreSQL '$ToolName' nao encontrada. Configure POSTGRES_BIN no .env."
}

function Assert-ExternalCommand {
  param([string]$Description)

  if ($LASTEXITCODE -ne 0) {
    throw "$Description falhou com codigo $LASTEXITCODE."
  }
}

function Remove-PartialFile {
  param([string]$Path)

  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    Remove-Item -LiteralPath $Path -Force
  }
}

if ($SkipDatabase -and $SkipMedia) {
  throw 'Nada para salvar: SkipDatabase e SkipMedia foram informados juntos.'
}

$envValues = Read-DotEnv -Path (Join-Path $repoRoot '.env')
$backupRoot = if ($OutputDir) { $OutputDir } else { Get-Setting -EnvValues $envValues -Name 'BACKUP_DIR' -Default (Join-Path $repoRoot 'backups') }
$backupRoot = (New-Item -ItemType Directory -Force -Path $backupRoot).FullName
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$created = @()
$python = Resolve-PythonExe -ProvidedPythonExe $PythonExe

if (-not $SkipDatabase) {
  $databaseUrl = Get-Setting -EnvValues $envValues -Name 'DATABASE_URL'
  $dbEngine = Get-Setting -EnvValues $envValues -Name 'DB_ENGINE' -Default 'django.db.backends.sqlite3'

  if ($databaseUrl -and ($databaseUrl.StartsWith('postgres://') -or $databaseUrl.StartsWith('postgresql://'))) {
    $dumpFile = Join-Path $backupRoot "itam-db-$timestamp.dump"
    $partialDumpFile = "$dumpFile.partial"
    $pgDump = Resolve-PostgresTool -EnvValues $envValues -ToolName 'pg_dump'
    $pgRestore = Resolve-PostgresTool -EnvValues $envValues -ToolName 'pg_restore'
    try {
      & $pgDump --format=custom --no-owner --no-acl --file $partialDumpFile $databaseUrl
      Assert-ExternalCommand -Description 'Backup do PostgreSQL'
      & $pgRestore --list $partialDumpFile | Out-Null
      Assert-ExternalCommand -Description 'Validacao do backup do PostgreSQL'
      Move-Item -LiteralPath $partialDumpFile -Destination $dumpFile -Force
      $created += $dumpFile
    } finally {
      Remove-PartialFile -Path $partialDumpFile
    }
  } elseif ($dbEngine -in @('django.db.backends.postgresql', 'postgres', 'postgresql')) {
    $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'itam'
    $dbUser = Get-Setting -EnvValues $envValues -Name 'DB_USER' -Default 'itam'
    $dbPassword = Get-Setting -EnvValues $envValues -Name 'DB_PASSWORD'
    $dbHost = Get-Setting -EnvValues $envValues -Name 'DB_HOST' -Default '127.0.0.1'
    $dbPort = Get-Setting -EnvValues $envValues -Name 'DB_PORT' -Default '5432'
    $dumpFile = Join-Path $backupRoot "itam-db-$timestamp.dump"
    $partialDumpFile = "$dumpFile.partial"
    $pgDump = Resolve-PostgresTool -EnvValues $envValues -ToolName 'pg_dump'
    $pgRestore = Resolve-PostgresTool -EnvValues $envValues -ToolName 'pg_restore'

    $oldPgPassword = $env:PGPASSWORD
    try {
      if ($dbPassword) {
        $env:PGPASSWORD = $dbPassword
      }
      & $pgDump --format=custom --no-owner --no-acl --host $dbHost --port $dbPort --username $dbUser --file $partialDumpFile $dbName
      Assert-ExternalCommand -Description 'Backup do PostgreSQL'
      & $pgRestore --list $partialDumpFile | Out-Null
      Assert-ExternalCommand -Description 'Validacao do backup do PostgreSQL'
      Move-Item -LiteralPath $partialDumpFile -Destination $dumpFile -Force
      $created += $dumpFile
    } finally {
      $env:PGPASSWORD = $oldPgPassword
      Remove-PartialFile -Path $partialDumpFile
    }
  } elseif ($dbEngine -in @('django.db.backends.sqlite3', 'sqlite', 'sqlite3')) {
    $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'db.sqlite3'
    $dbPath = if ([System.IO.Path]::IsPathRooted($dbName)) { $dbName } else { Join-Path $repoRoot $dbName }
    if (-not (Test-Path $dbPath)) {
      throw "Banco SQLite nao encontrado: $dbPath"
    }
    $sqliteFile = Join-Path $backupRoot "itam-db-$timestamp.sqlite3"
    $partialSqliteFile = "$sqliteFile.partial"
    $sqliteBackupCommand = 'import sqlite3, sys; source = sqlite3.connect(sys.argv[1]); target = sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()'
    try {
      & $python -c $sqliteBackupCommand $dbPath $partialSqliteFile
      Assert-ExternalCommand -Description 'Backup do SQLite'
      Move-Item -LiteralPath $partialSqliteFile -Destination $sqliteFile -Force
      $created += $sqliteFile
    } finally {
      Remove-PartialFile -Path $partialSqliteFile
    }
  } else {
    throw "Backup automatico nao implementado para DB_ENGINE=$dbEngine."
  }
}

if (-not $SkipMedia) {
  $mediaDir = Join-Path $repoRoot 'media'
  if (Test-Path $mediaDir) {
    $mediaItems = @(Get-ChildItem -LiteralPath $mediaDir -Force)
    $mediaBackupItems = if ($IncludeGeneratedQrCodes) {
      $mediaItems
    } else {
      @($mediaItems | Where-Object { $_.Name -ne 'qrcodes' })
    }
    if ($mediaBackupItems.Count -gt 0) {
      $mediaFile = Join-Path $backupRoot "itam-media-$timestamp.zip"
      $partialMediaFile = Join-Path $backupRoot "itam-media-$timestamp.partial.zip"
      try {
        $tarCommand = Get-Command tar.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($tarCommand) {
          $tarArguments = @('-a', '-c', '-f', $partialMediaFile, '-C', $mediaDir)
          if (-not $IncludeGeneratedQrCodes) {
            $tarArguments += @('--exclude=./qrcodes', '--exclude=./qrcodes/*')
          }
          $tarArguments += '.'
          & $tarCommand.Source @tarArguments
          Assert-ExternalCommand -Description 'Compactacao da midia'
        } else {
          Compress-Archive -LiteralPath $mediaBackupItems.FullName -DestinationPath $partialMediaFile -Force
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($partialMediaFile)
        try {
          if ($archive.Entries.Count -eq 0) {
            throw 'O arquivo de midia gerado esta vazio.'
          }
        } finally {
          $archive.Dispose()
        }
        Move-Item -LiteralPath $partialMediaFile -Destination $mediaFile -Force
        $created += $mediaFile
      } finally {
        Remove-PartialFile -Path $partialMediaFile
      }
    } else {
      Write-Warning "Diretorio media nao possui arquivos persistentes. Nenhum arquivo de media foi compactado."
    }
  } else {
    Write-Warning "Diretorio media nao encontrado: $mediaDir"
  }
}

if ($created.Count -eq 0) {
  throw 'Nenhum arquivo de backup foi criado.'
}

$manifestFile = Join-Path $backupRoot "itam-backup-$timestamp.manifest.txt"
$manifestLines = @(
  "created_at=$(Get-Date -Format o)",
  "repo_root=$repoRoot",
  "python=$python",
  "retention_days=$RetentionDays",
  "generated_qrcodes_included=$([bool]$IncludeGeneratedQrCodes)",
  'status=complete',
  "files=$($created -join ';')"
)
foreach ($file in $created) {
  $item = Get-Item -LiteralPath $file
  $hash = Get-FileHash -LiteralPath $file -Algorithm SHA256
  $manifestLines += "file=$($item.Name)|$($item.Length)|$($hash.Hash)"
}
$manifestLines | Set-Content -LiteralPath $manifestFile -Encoding UTF8
$created += $manifestFile

$removed = @()
if ($RetentionDays -gt 0) {
  $cutoff = (Get-Date).AddDays(-$RetentionDays)
  $backupPatterns = @('itam-db-*.dump', 'itam-db-*.sqlite3', 'itam-media-*.zip', 'itam-backup-*.manifest.txt')
  foreach ($pattern in $backupPatterns) {
    $expiredFiles = Get-ChildItem -LiteralPath $backupRoot -File -Filter $pattern -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTime -lt $cutoff }
    foreach ($expiredFile in $expiredFiles) {
      Remove-Item -LiteralPath $expiredFile.FullName -Force
      $removed += $expiredFile.FullName
    }
  }
}
$partialCutoff = (Get-Date).AddDays(-1)
$stalePartialFiles = Get-ChildItem -LiteralPath $backupRoot -File -Filter 'itam-*.partial*' -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -lt $partialCutoff }
foreach ($stalePartialFile in $stalePartialFiles) {
  Remove-Item -LiteralPath $stalePartialFile.FullName -Force
  $removed += $stalePartialFile.FullName
}

Write-Host 'Backup concluido:'
foreach ($file in $created) {
  Write-Host "  $file"
}
if ($removed.Count -gt 0) {
  Write-Host "Arquivos expirados removidos: $($removed.Count)"
}
