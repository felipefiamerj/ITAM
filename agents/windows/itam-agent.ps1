param(
  [string]$ConfigPath = '',
  [string]$BaseUrl = '',
  [string]$AgentToken = '',
  [string]$AssetId = '',
  [string]$ServiceTag = '',
  [string]$SerialNumber = '',
  [int]$TimeoutSec = 30,
  [int]$BatteryWarningPercent = 25,
  [int]$BatteryCriticalPercent = 15,
  [int]$DiskWarningFreePercent = 10,
  [int]$DiskCriticalFreePercent = 5,
  [switch]$Loop,
  [int]$IntervalSeconds = 300,
  [switch]$PrintPayload
)

$ErrorActionPreference = 'Stop'

if (-not $ConfigPath) {
  $ConfigPath = Join-Path $PSScriptRoot 'itam-agent.config.json'
}

function ConvertTo-Hashtable {
  param([object]$InputObject)

  $table = @{}
  if (-not $InputObject) {
    return $table
  }

  foreach ($property in $InputObject.PSObject.Properties) {
    $table[$property.Name] = $property.Value
  }
  return $table
}

function Read-AgentConfig {
  param([string]$Path)

  if (-not (Test-Path $Path)) {
    return @{}
  }

  $raw = Get-Content -LiteralPath $Path -Raw
  if (-not $raw.Trim()) {
    return @{}
  }

  return ConvertTo-Hashtable -InputObject ($raw | ConvertFrom-Json)
}

function Get-ConfigValue {
  param(
    [hashtable]$Config,
    [string]$Name,
    [object]$CurrentValue
  )

  if ($null -ne $CurrentValue -and "$CurrentValue" -ne '') {
    return $CurrentValue
  }
  if ($Config.ContainsKey($Name) -and $null -ne $Config[$Name] -and "$($Config[$Name])" -ne '') {
    return $Config[$Name]
  }
  return $CurrentValue
}

function Get-PrimaryIPv4 {
  try {
    $address = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
      Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } |
      Sort-Object InterfaceMetric |
      Select-Object -First 1 -ExpandProperty IPAddress
    if ($address) {
      return $address
    }
  } catch {
  }

  try {
    return [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
      Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and $_.IPAddressToString -ne '127.0.0.1' } |
      Select-Object -First 1 -ExpandProperty IPAddressToString
  } catch {
    return ''
  }
}

function Get-SafeCimInstance {
  param(
    [string]$ClassName,
    [string]$Filter = ''
  )

  try {
    if ($Filter) {
      return Get-CimInstance -ClassName $ClassName -Filter $Filter -ErrorAction Stop
    }
    return Get-CimInstance -ClassName $ClassName -ErrorAction Stop
  } catch {
    return $null
  }
}

function Get-DiskSummary {
  $disks = @(Get-SafeCimInstance -ClassName 'Win32_LogicalDisk' -Filter 'DriveType=3')
  if (-not $disks -or $disks.Count -eq 0) {
    try {
      $driveItems = @()
      foreach ($drive in [System.IO.DriveInfo]::GetDrives()) {
        if ($drive.DriveType -ne [System.IO.DriveType]::Fixed -or -not $drive.IsReady -or $drive.TotalSize -le 0) {
          continue
        }
        $freePercent = [math]::Round(([double]$drive.AvailableFreeSpace / [double]$drive.TotalSize) * 100, 2)
        $driveItems += @{
          name = $drive.Name
          size_gb = [math]::Round(([double]$drive.TotalSize / 1GB), 2)
          free_gb = [math]::Round(([double]$drive.AvailableFreeSpace / 1GB), 2)
          free_percent = $freePercent
        }
      }
      if ($driveItems.Count -gt 0) {
        return @{
          disks = $driveItems
          min_free_percent = ($driveItems | Sort-Object free_percent | Select-Object -First 1).free_percent
        }
      }
    } catch {
    }
    return @{
      disks = @()
      min_free_percent = $null
    }
  }

  $items = @()
  foreach ($disk in $disks) {
    if (-not $disk.Size -or [double]$disk.Size -le 0) {
      continue
    }
    $freePercent = [math]::Round(([double]$disk.FreeSpace / [double]$disk.Size) * 100, 2)
    $items += @{
      name = $disk.DeviceID
      size_gb = [math]::Round(([double]$disk.Size / 1GB), 2)
      free_gb = [math]::Round(([double]$disk.FreeSpace / 1GB), 2)
      free_percent = $freePercent
    }
  }

  $minFree = $null
  if ($items.Count -gt 0) {
    $minFree = ($items | Sort-Object free_percent | Select-Object -First 1).free_percent
  }

  return @{
    disks = $items
    min_free_percent = $minFree
  }
}

function Build-TelemetryPayload {
  param(
    [string]$ResolvedAgentToken,
    [string]$ResolvedAssetId,
    [string]$ResolvedServiceTag,
    [string]$ResolvedSerialNumber,
    [int]$ResolvedBatteryWarningPercent,
    [int]$ResolvedBatteryCriticalPercent,
    [int]$ResolvedDiskWarningFreePercent,
    [int]$ResolvedDiskCriticalFreePercent
  )

  $computer = Get-SafeCimInstance -ClassName 'Win32_ComputerSystem'
  $bios = Get-SafeCimInstance -ClassName 'Win32_BIOS'
  $os = Get-SafeCimInstance -ClassName 'Win32_OperatingSystem'
  $processor = Get-SafeCimInstance -ClassName 'Win32_Processor' | Select-Object -First 1
  $battery = Get-SafeCimInstance -ClassName 'Win32_Battery' | Select-Object -First 1
  $diskSummary = Get-DiskSummary

  $hostName = [System.Net.Dns]::GetHostName()
  $serial = if ($ResolvedSerialNumber) { $ResolvedSerialNumber } elseif ($bios -and $bios.SerialNumber) { "$($bios.SerialNumber)".Trim() } else { '' }
  $service = if ($ResolvedServiceTag) { $ResolvedServiceTag } else { $serial }
  $batteryLevel = $null
  if ($battery -and $null -ne $battery.EstimatedChargeRemaining) {
    $batteryLevel = [int]$battery.EstimatedChargeRemaining
  }

  $severity = 'info'
  $eventType = 'heartbeat'
  $messages = @()

  if ($null -ne $batteryLevel) {
    if ($batteryLevel -le $ResolvedBatteryCriticalPercent) {
      $severity = 'critical'
      $eventType = 'bateria_baixa'
      $messages += "Bateria critica em $batteryLevel%."
    } elseif ($batteryLevel -le $ResolvedBatteryWarningPercent -and $severity -ne 'critical') {
      $severity = 'warning'
      $eventType = 'bateria_baixa'
      $messages += "Bateria baixa em $batteryLevel%."
    }
  }

  if ($null -ne $diskSummary.min_free_percent) {
    if ($diskSummary.min_free_percent -le $ResolvedDiskCriticalFreePercent) {
      $severity = 'critical'
      $eventType = 'health_warning'
      $messages += "Disco com espaco critico: $($diskSummary.min_free_percent)% livre."
    } elseif ($diskSummary.min_free_percent -le $ResolvedDiskWarningFreePercent -and $severity -ne 'critical') {
      $severity = 'warning'
      $eventType = 'health_warning'
      $messages += "Disco com pouco espaco: $($diskSummary.min_free_percent)% livre."
    }
  }

  if ($messages.Count -eq 0) {
    $messages += "Heartbeat recebido de $hostName."
  }

  $totalMemoryMb = $null
  if ($computer -and $computer.TotalPhysicalMemory) {
    $totalMemoryMb = [math]::Round(([double]$computer.TotalPhysicalMemory / 1MB), 0)
  }

  $freeMemoryMb = $null
  if ($os -and $os.FreePhysicalMemory) {
    $freeMemoryMb = [math]::Round(([double]$os.FreePhysicalMemory / 1024), 0)
  }

  $lastBoot = $null
  if ($os -and $os.LastBootUpTime) {
    $lastBoot = ([datetime]$os.LastBootUpTime).ToString('o')
  }

  $loggedUser = ''
  if ($computer -and $computer.UserName) {
    $loggedUser = $computer.UserName
  } elseif ($env:USERNAME) {
    $loggedUser = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
  }

  $osCaption = if ($os -and $os.Caption) { $os.Caption } else { [System.Environment]::OSVersion.VersionString }
  $osVersion = if ($os -and $os.Version) { $os.Version } else { [System.Environment]::OSVersion.Version.ToString() }

  return @{
    agent_token = $ResolvedAgentToken
    host_name = $hostName
    ip = Get-PrimaryIPv4
    metadata = @{
      agent_name = 'itam-windows-agent'
      agent_version = '2026.1'
      machine_domain = if ($computer) { $computer.Domain } else { '' }
      manufacturer = if ($computer) { $computer.Manufacturer } else { '' }
      model = if ($computer) { $computer.Model } else { '' }
    }
    devices = @(
      @{
        id_patrimonio = $ResolvedAssetId
        service_tag = $service
        numero_serie = $serial
        serial = $serial
        event_type = $eventType
        severity = $severity
        message = ($messages -join ' ')
        battery_level = $batteryLevel
        battery_status = if ($battery) { $battery.BatteryStatus } else { $null }
        disk_free_percent = $diskSummary.min_free_percent
        disks = $diskSummary.disks
        cpu_load_percent = if ($processor) { $processor.LoadPercentage } else { $null }
        memory_total_mb = $totalMemoryMb
        memory_free_mb = $freeMemoryMb
        logged_user = $loggedUser
        os_caption = $osCaption
        os_version = $osVersion
        last_boot_at = $lastBoot
        collected_at = (Get-Date).ToUniversalTime().ToString('o')
      }
    )
  }
}

function Send-Telemetry {
  param([hashtable]$Payload, [string]$ResolvedBaseUrl, [int]$ResolvedTimeoutSec)

  $url = $ResolvedBaseUrl.TrimEnd('/') + '/api/telemetria/ingestao/'
  $json = $Payload | ConvertTo-Json -Depth 10
  if ($PrintPayload) {
    Write-Host $json
  }

  $headers = @{
    'X-ITAM-AGENT-TOKEN' = $Payload.agent_token
  }

  $response = Invoke-RestMethod -Method Post -Uri $url -Headers $headers -ContentType 'application/json' -Body $json -TimeoutSec $ResolvedTimeoutSec
  Write-Host "Telemetria enviada: processados=$($response.processados) alertas=$($response.alertas) erros=$($response.erros.Count)"
}

$config = Read-AgentConfig -Path $ConfigPath
$BaseUrl = Get-ConfigValue -Config $config -Name 'BaseUrl' -CurrentValue $BaseUrl
$AgentToken = Get-ConfigValue -Config $config -Name 'AgentToken' -CurrentValue $AgentToken
$AssetId = Get-ConfigValue -Config $config -Name 'AssetId' -CurrentValue $AssetId
$ServiceTag = Get-ConfigValue -Config $config -Name 'ServiceTag' -CurrentValue $ServiceTag
$SerialNumber = Get-ConfigValue -Config $config -Name 'SerialNumber' -CurrentValue $SerialNumber
$TimeoutSec = [int](Get-ConfigValue -Config $config -Name 'TimeoutSec' -CurrentValue $TimeoutSec)
$BatteryWarningPercent = [int](Get-ConfigValue -Config $config -Name 'BatteryWarningPercent' -CurrentValue $BatteryWarningPercent)
$BatteryCriticalPercent = [int](Get-ConfigValue -Config $config -Name 'BatteryCriticalPercent' -CurrentValue $BatteryCriticalPercent)
$DiskWarningFreePercent = [int](Get-ConfigValue -Config $config -Name 'DiskWarningFreePercent' -CurrentValue $DiskWarningFreePercent)
$DiskCriticalFreePercent = [int](Get-ConfigValue -Config $config -Name 'DiskCriticalFreePercent' -CurrentValue $DiskCriticalFreePercent)

if (-not $BaseUrl) {
  throw 'BaseUrl nao foi informado.'
}
if (-not $AgentToken) {
  throw 'AgentToken nao foi informado.'
}
if (-not $AssetId -and -not $ServiceTag -and -not $SerialNumber) {
  Write-Warning 'Nenhum AssetId, ServiceTag ou SerialNumber foi informado. O backend so vai processar se encontrar o equipamento por identificador coletado automaticamente.'
}

do {
  $payload = Build-TelemetryPayload `
    -ResolvedAgentToken $AgentToken `
    -ResolvedAssetId $AssetId `
    -ResolvedServiceTag $ServiceTag `
    -ResolvedSerialNumber $SerialNumber `
    -ResolvedBatteryWarningPercent $BatteryWarningPercent `
    -ResolvedBatteryCriticalPercent $BatteryCriticalPercent `
    -ResolvedDiskWarningFreePercent $DiskWarningFreePercent `
    -ResolvedDiskCriticalFreePercent $DiskCriticalFreePercent

  Send-Telemetry -Payload $payload -ResolvedBaseUrl $BaseUrl -ResolvedTimeoutSec $TimeoutSec

  if ($Loop) {
    Start-Sleep -Seconds $IntervalSeconds
  }
} while ($Loop)
