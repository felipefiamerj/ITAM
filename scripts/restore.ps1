param(
  [Parameter(Mandatory = $true)]
  [string]$DatabaseBackup,
  [string]$MediaBackup = '',
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

$envValues = Read-DotEnv -Path (Join-Path $repoRoot '.env')
$databaseUrl = Get-Setting -EnvValues $envValues -Name 'DATABASE_URL'
$dbEngine = Get-Setting -EnvValues $envValues -Name 'DB_ENGINE' -Default 'django.db.backends.sqlite3'
$dbBackupPath = (Resolve-Path $DatabaseBackup).Path

if ($databaseUrl -and ($databaseUrl.StartsWith('postgres://') -or $databaseUrl.StartsWith('postgresql://'))) {
  & pg_restore --clean --if-exists --no-owner --no-acl --dbname $databaseUrl $dbBackupPath
} elseif ($dbEngine -eq 'django.db.backends.postgresql') {
  $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'itam'
  $dbUser = Get-Setting -EnvValues $envValues -Name 'DB_USER' -Default 'itam'
  $dbPassword = Get-Setting -EnvValues $envValues -Name 'DB_PASSWORD'
  $dbHost = Get-Setting -EnvValues $envValues -Name 'DB_HOST' -Default '127.0.0.1'
  $dbPort = Get-Setting -EnvValues $envValues -Name 'DB_PORT' -Default '5432'

  $oldPgPassword = $env:PGPASSWORD
  try {
    if ($dbPassword) {
      $env:PGPASSWORD = $dbPassword
    }
    & pg_restore --clean --if-exists --no-owner --no-acl --host $dbHost --port $dbPort --username $dbUser --dbname $dbName $dbBackupPath
  } finally {
    $env:PGPASSWORD = $oldPgPassword
  }
} elseif ($dbEngine -eq 'django.db.backends.sqlite3') {
  $dbName = Get-Setting -EnvValues $envValues -Name 'DB_NAME' -Default 'db.sqlite3'
  $dbPath = if ([System.IO.Path]::IsPathRooted($dbName)) { $dbName } else { Join-Path $repoRoot $dbName }
  if (Test-Path $dbPath) {
    $safetyCopy = "$dbPath.pre-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $dbPath -Destination $safetyCopy
    Write-Host "Copia de seguranca criada: $safetyCopy"
  }
  Copy-Item -LiteralPath $dbBackupPath -Destination $dbPath -Force
} else {
  throw "Restore automatico nao implementado para DB_ENGINE=$dbEngine."
}

if ($MediaBackup) {
  if (-not (Test-Path $MediaBackup)) {
    throw "Arquivo de media nao encontrado: $MediaBackup"
  }
  $mediaDir = Join-Path $repoRoot 'media'
  New-Item -ItemType Directory -Force -Path $mediaDir | Out-Null
  Expand-Archive -LiteralPath $MediaBackup -DestinationPath $mediaDir -Force
}

Write-Host 'Restore concluido. Rode manage.py migrate e deploy-check antes de liberar o sistema.'
