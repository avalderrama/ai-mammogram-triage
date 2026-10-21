<#
.SYNOPSIS
  Starts the pilot: PWA preview + two Cloudflare quick tunnels (app and MCP) + the MCP server configured for them.

.DESCRIPTION
  Quick-tunnel URLs change on every start, so this script reads them from cloudflared's log and passes them to
  the server (MAMMO_ALLOWED_HOSTS / MAMMO_ALLOWED_ORIGINS / MAMMO_TRUST_PROXY_HEADERS). The API key and the
  model folder come from the repository's .env (never committed). Press Ctrl+C to stop everything.

  Do not put test images in the app's dist/ folder while the tunnel is open: everything there is public.

.EXAMPLE
  .\scripts\start_pilot.ps1
#>
param(
  [string]$Cloudflared = "$env:USERPROFILE\tools\cloudflared\2026.10.0\cloudflared.exe",
  [string]$Python = "$env:USERPROFILE\.venvs\birads-poc\Scripts\python.exe",
  [string]$AppDir = (Join-Path $PSScriptRoot "..\..\ai-mammogram-triage-app"),
  [string]$LogDir = "$env:USERPROFILE\mammo-data\logs"
)
$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force $LogDir | Out-Null
foreach ($tool in @($Cloudflared, $Python)) { if (-not (Test-Path $tool)) { throw "Not found: $tool" } }

function Start-Tunnel([string]$name, [int]$port) {
  $log = Join-Path $LogDir "tunnel_$name.log"
  if (Test-Path $log) { Remove-Item $log -Confirm:$false }
  $proc = Start-Process $Cloudflared -ArgumentList "tunnel --no-autoupdate --url http://127.0.0.1:$port" `
    -RedirectStandardError $log -RedirectStandardOutput "$log.out" -WindowStyle Hidden -PassThru
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    $match = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($match) { return @{ Process = $proc; Url = $match.Matches[0].Value } }
  }
  throw "The $name tunnel did not report a URL; see $log"
}

$processes = @()
try {
  Write-Host "Building and serving the PWA on 127.0.0.1:4173..."
  Push-Location $AppDir
  npm run build | Out-Null
  Pop-Location
  $processes += Start-Process "npm.cmd" -ArgumentList "--prefix `"$AppDir`" run preview" -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir "pwa_preview.log") -RedirectStandardError (Join-Path $LogDir "pwa_preview.err.log")

  $app = Start-Tunnel "pwa" 4173; $processes += $app.Process
  $mcp = Start-Tunnel "mcp" 8765; $processes += $mcp.Process
  $mcpHost = ([uri]$mcp.Url).Host

  $env:MAMMO_ALLOWED_HOSTS = "127.0.0.1:*,localhost:*,$mcpHost"
  $env:MAMMO_ALLOWED_ORIGINS = "$($app.Url),http://127.0.0.1:4173,http://localhost:4173"
  $env:MAMMO_TRUST_PROXY_HEADERS = "true"
  Write-Host ""
  Write-Host "App for the doctors : $($app.Url)"
  Write-Host "Server (Settings)   : $($mcp.Url)/mcp"
  Write-Host "Access key          : MAMMO_API_KEY in $repo\.env (share it through a private channel)"
  Write-Host ""
  Write-Host "Starting the MCP server (Ctrl+C stops everything)..."
  Push-Location $repo
  & $Python -m mammo_host http
}
finally {
  Pop-Location -ErrorAction SilentlyContinue
  foreach ($p in $processes) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -Confirm:$false } }
  Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
    Where-Object { $_.CommandLine -match 'vite.*preview' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
  Write-Host "Pilot stopped."
}
