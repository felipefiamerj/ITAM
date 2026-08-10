param(
  [string]$BaseUrl = 'http://127.0.0.1:8000',
  [string]$ApiKey = ''
)

$ErrorActionPreference = 'Stop'
$base = $BaseUrl.TrimEnd('/')

function Test-Endpoint {
  param(
    [string]$Path,
    [hashtable]$Headers = @{}
  )

  $url = "$base$Path"
  $response = Invoke-WebRequest -UseBasicParsing -Uri $url -Headers $Headers -TimeoutSec 15
  if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
    throw "Falha em $url com status $($response.StatusCode)."
  }
  Write-Host "OK $($response.StatusCode) $url"
}

Test-Endpoint -Path '/health/'
Test-Endpoint -Path '/accounts/login/'

if ($ApiKey) {
  Test-Endpoint -Path '/api/schema/' -Headers @{ 'X-ITAM-API-Key' = $ApiKey }
}

Write-Host 'Smoke test concluido.'
