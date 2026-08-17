param(
  [Parameter(Mandatory = $true)]
  [string]$ConfigPath,
  [ValidateRange(30, 3600)]
  [int]$IntervalSeconds = 300
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$agentScript = Join-Path $repoRoot 'agents\windows\itam-agent.ps1'
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path

if (-not (Test-Path -LiteralPath $agentScript -PathType Leaf)) {
  throw "Agente Windows nao encontrado: $agentScript"
}

Write-Output "$(Get-Date -Format o) Supervisor do agente iniciado. Intervalo: $IntervalSeconds segundos."

while ($true) {
  $startedAt = Get-Date
  try {
    $output = & powershell.exe `
      -NoProfile `
      -NonInteractive `
      -ExecutionPolicy Bypass `
      -File $agentScript `
      -ConfigPath $resolvedConfig 2>&1
    $exitCode = $LASTEXITCODE
    $summary = ($output | Select-Object -Last 1)
    if ($exitCode -eq 0) {
      Write-Output "$(Get-Date -Format o) Heartbeat concluido. $summary"
    } else {
      Write-Error "Heartbeat falhou com codigo $exitCode. $summary" -ErrorAction Continue
    }
  } catch {
    Write-Error "Heartbeat falhou: $($_.Exception.Message)" -ErrorAction Continue
  }

  $elapsedSeconds = [Math]::Max(0, ((Get-Date) - $startedAt).TotalSeconds)
  $waitSeconds = [Math]::Max(1, $IntervalSeconds - [int]$elapsedSeconds)
  Start-Sleep -Seconds $waitSeconds
}
