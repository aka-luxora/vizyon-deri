$ErrorActionPreference = 'Stop'
$moduleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $moduleRoot
docker compose up --build -d
Write-Host 'Vizyon Deri Planlama hazır: http://localhost:8787' -ForegroundColor Green
