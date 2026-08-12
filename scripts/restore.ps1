param(
  [Parameter(Mandatory = $true)]
  [string]$DatabaseBackup,
  [string]$MediaBackup = '',
  [switch]$ReplaceMedia,
  [string]$ConfirmRestore = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repoRoot

if ($ConfirmRestore -ne 'RESTORE') {
  throw 'Restore cancelado. Execute com -ConfirmRestore RESTORE para confirmar a operacao destrutiva.'
}

if (-not (Test-Path $DatabaseBackup)) {
  throw "Arquivo de backup nao encontrado: $DatabaseBackup"
}

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
    $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
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
  $venvCandidates = @(
    (Join-Path $repoRoot '.venv312\Scripts\python.exe'),
    (Join-Path $repoRoot '.venv\Scripts\python.exe'),
    (Join-Path $repoRoot '.venv313\Scripts\python.exe')
  )

  foreach ($candidate in $venvCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      return (Resolve-Path -LiteralPath $candidate).Path
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

$envValues = Read-DotEnv -Path (Join-Path $repoRoot '.env')
$databaseUrl = Get-Setting -EnvValues $envValues -Name 'DATABASE_URL'
$dbEngine = Get-Setting -EnvValues $envValues -Name 'DB_ENGINE' -Default 'django.db.backends.sqlite3'
$dbBackupPath = (Resolve-Path $DatabaseBackup).Path
$mediaBackupPath = ''

if ($MediaBackup) {
  if (-not (Test-Path $MediaBackup)) {
    throw "Arquivo de media nao encontrado: $MediaBackup"
  }
  $mediaBackupPath = (Resolve-Path $MediaBackup).Path
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [System.IO.Compression.ZipFile]::OpenRead($mediaBackupPath)
  try {
    if ($archive.Entries.Count -eq 0) {
      throw 'O arquivo de midia informado esta vazio.'
    }
  } finally {
    $archive.Dispose()
  }
}

if ($databaseUrl -and ($databaseUrl.StartsWith('postgres://') -or $databaseUrl.StartsWith('postgresql://'))) {
  $pgRestore = Resolve-PostgresTool -EnvValues $envValues -ToolName 'pg_restore'
  & $pgRestore --list $dbBackupPath | Out-Null
  Assert-ExternalCommand -Description 'Validacao do backup do PostgreSQL'
  & $pgRestore --clean --if-exists --no-owner --no-acl --dbname $databaseUrl $dbBackupPath
  Assert-ExternalCommand -Description 'Restore do PostgreSQL'
} elseif ($dbEngine -in @('django.db.backends.postgresql', 'postgres', 'postgresql')) {
  $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'itam'
  $dbUser = Get-Setting -EnvValues $envValues -Name 'DB_USER' -Default 'itam'
  $dbPassword = Get-Setting -EnvValues $envValues -Name 'DB_PASSWORD'
  $dbHost = Get-Setting -EnvValues $envValues -Name 'DB_HOST' -Default '127.0.0.1'
  $dbPort = Get-Setting -EnvValues $envValues -Name 'DB_PORT' -Default '5432'
  $pgRestore = Resolve-PostgresTool -EnvValues $envValues -ToolName 'pg_restore'

  & $pgRestore --list $dbBackupPath | Out-Null
  Assert-ExternalCommand -Description 'Validacao do backup do PostgreSQL'

  $oldPgPassword = $env:PGPASSWORD
  try {
    if ($dbPassword) {
      $env:PGPASSWORD = $dbPassword
    }
    & $pgRestore --clean --if-exists --no-owner --no-acl --host $dbHost --port $dbPort --username $dbUser --dbname $dbName $dbBackupPath
    Assert-ExternalCommand -Description 'Restore do PostgreSQL'
  } finally {
    $env:PGPASSWORD = $oldPgPassword
  }
} elseif ($dbEngine -in @('django.db.backends.sqlite3', 'sqlite', 'sqlite3')) {
  $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'db.sqlite3'
  $dbPath = if ([System.IO.Path]::IsPathRooted($dbName)) { $dbName } else { Join-Path $repoRoot $dbName }
  $python = Resolve-PythonExe
  $sqliteCheckCommand = "import sqlite3, sys; connection = sqlite3.connect(sys.argv[1]); result = connection.execute('PRAGMA integrity_check').fetchone()[0]; connection.close(); raise SystemExit(0 if result == 'ok' else 1)"
  & $python -c $sqliteCheckCommand $dbBackupPath
  Assert-ExternalCommand -Description 'Validacao do backup do SQLite'
  if (Test-Path $dbPath) {
    $safetyCopy = "$dbPath.pre-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $dbPath -Destination $safetyCopy
    Write-Host "Copia de seguranca criada: $safetyCopy"
  }
  Copy-Item -LiteralPath $dbBackupPath -Destination $dbPath -Force
} else {
  throw "Restore automatico nao implementado para DB_ENGINE=$dbEngine."
}

if ($mediaBackupPath) {
  $mediaDir = Join-Path $repoRoot 'media'
  if ($ReplaceMedia -and (Test-Path -LiteralPath $mediaDir)) {
    $resolvedMediaDir = (Resolve-Path -LiteralPath $mediaDir).Path
    $expectedMediaDir = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'media'))
    if ($resolvedMediaDir -ne $expectedMediaDir -or -not $resolvedMediaDir.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Diretorio de media fora do repositorio: $resolvedMediaDir"
    }
    Get-ChildItem -LiteralPath $resolvedMediaDir -Force | Remove-Item -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $mediaDir | Out-Null
  Expand-Archive -LiteralPath $mediaBackupPath -DestinationPath $mediaDir -Force
  Write-Warning 'QR Codes gerados nao fazem parte do backup padrao. Execute: python manage.py regenerar_qrcodes --force'
}

Write-Host 'Restore concluido. Rode manage.py migrate e deploy-check antes de liberar o sistema.'
