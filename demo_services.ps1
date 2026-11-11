# Native Windows twin of demo_services.sh - same behavior, keep the two in sync.
# Spawns everything demo.ipynb and the web client (bdc-assist-client) need:
#   1. r-doc-mcp MCP server (HTTP) from DOC_MCP_DIR (.\.env, default ..\bdc-doc-mcp) on MCP_PORT
#      (that repo's .env, default 8001), health-checked at the r_doc_mcp url in
#      <CONFIG_DIR>\mcp_servers.yaml - the URL r-assist actually connects to
#   2. artie-mcp MCP server (HTTP) from ARTIE_MCP_DIR (.\.env, default ..\artie-mcp), only when
#      <CONFIG_DIR>\mcp_servers.yaml has an artie_mcp block (examples\bdc), health-checked at its url
#   3. r-assist API on API_PORT (.\.env, default 8010)
# Embedding/LLM endpoints are whatever the two .env files point at; start those yourself.
# Services already running are left alone; Ctrl-C stops only what this script started.
# Each service gets START_TIMEOUT seconds (.\.env, default 30) to answer.
# Logs: %TEMP%\r_*.log
# ASCII only: PowerShell 5.1 misparses BOM-less UTF-8.
Set-Location $PSScriptRoot

function Get-DotEnv([string]$file, [string]$key, [string]$default) {
  # shell env wins, then the .env, then default - same precedence as load_dotenv
  $v = [Environment]::GetEnvironmentVariable($key)
  if (-not $v -and (Test-Path $file)) {
    $hit = Select-String -Path $file -Pattern ('^' + $key + '=') | Select-Object -Last 1
    if ($hit) {
      $v = ($hit.Line -replace ('^' + $key + '='), '' -replace '\s*#.*$', '').Trim().Trim('"').Trim("'")
    }
  }
  if ($v) { $v } else { $default }
}

$DOC_MCP_DIR     = Get-DotEnv '.\.env' 'DOC_MCP_DIR' '..\bdc-doc-mcp'
$ARTIE_MCP_DIR   = Get-DotEnv '.\.env' 'ARTIE_MCP_DIR' '..\artie-mcp'
$START_TIMEOUT   = Get-DotEnv '.\.env' 'START_TIMEOUT' '30'
$MCP_PORT        = Get-DotEnv (Join-Path $DOC_MCP_DIR '.env') 'MCP_PORT' '8001'
$API_PORT        = Get-DotEnv '.\.env' 'API_PORT' '8010'
$CONFIG_DIR      = Get-DotEnv '.\.env' 'CONFIG_DIR' 'config'

function Get-McpUrl([string]$file, [string]$server, [string]$default) {
  # the yaml block's url:, then default - twin of mcp_url in the .sh
  $v = $null
  if (Test-Path $file) {
    $in = $false
    foreach ($line in Get-Content $file) {
      if ($line -match ('^' + $server + ':')) { $in = $true; continue }
      if ($in -and $line -match '^[^\s#]') { break }
      if ($in -and $line -match '^\s*url:\s*(.*)$') { $v = ($Matches[1] -replace '\s*#.*$', '').Trim().Trim('"').Trim("'"); break }
    }
  }
  if ($v) { $v } else { $default }
}
$DOC_RAG_MCP_URL = [Environment]::GetEnvironmentVariable('DOC_RAG_MCP_URL')  # shell wins: test hook
if (-not $DOC_RAG_MCP_URL) { $DOC_RAG_MCP_URL = Get-McpUrl (Join-Path $CONFIG_DIR 'mcp_servers.yaml') 'r_doc_mcp' 'http://127.0.0.1:8001/mcp' }
$ARTIE_MCP_URL   = Get-McpUrl (Join-Path $CONFIG_DIR 'mcp_servers.yaml') 'artie_mcp' ''  # empty: this config doesn't use artie-mcp

function Test-Http([string]$url) {  # any HTTP response counts, even 4xx
  try {
    $req = [System.Net.WebRequest]::Create($url)
    $req.Timeout = 2000
    $req.GetResponse().Close()
    $true
  } catch [System.Net.WebException] {
    [bool]$_.Exception.InnerException.Response -or [bool]$_.Exception.Response
  } catch { $false }
}

function Wait-Http([string]$name, [string]$url, [int]$tries, [string]$hint) {
  for ($i = 0; $i -lt $tries; $i++) {
    if (Test-Http $url) { Write-Host "${name}: up ($url)"; return }
    Start-Sleep -Seconds 1
  }
  Write-Host "${name}: not answering at $url - $hint"
  exit 1  # finally below still runs and reaps whatever was started
}

$procs = @()
function Start-Bg([string]$cmdline, [string]$log) {
  # via cmd /c so stdout+stderr land in one log and taskkill /T reaps the whole tree
  $script:procs += Start-Process -FilePath 'cmd.exe' -ArgumentList "/c $cmdline > `"$log`" 2>&1" `
    -NoNewWindow -PassThru -WorkingDirectory $PSScriptRoot
}

try {
  # 1. doc MCP server
  if (Test-Http $DOC_RAG_MCP_URL) {
    Write-Host "r-doc-mcp: already running ($DOC_RAG_MCP_URL)"
  } else {
    Start-Bg "uv run --directory `"$DOC_MCP_DIR`" python -m r_doc_mcp.mcp_server --http" "$env:TEMP\r_doc_mcp.log"
    Wait-Http 'r-doc-mcp' $DOC_RAG_MCP_URL $START_TIMEOUT `
      "server binds MCP_PORT=$MCP_PORT; if that mismatches the url in $CONFIG_DIR\mcp_servers.yaml, fix it (see $env:TEMP\r_doc_mcp.log)"
  }

  # 2. artie-mcp, if the config uses it
  if (-not $ARTIE_MCP_URL) {
  } elseif (Test-Http $ARTIE_MCP_URL) {
    Write-Host "artie-mcp: already running ($ARTIE_MCP_URL)"
  } elseif (-not (Test-Path $ARTIE_MCP_DIR)) {
    Write-Host "artie-mcp: no checkout at $ARTIE_MCP_DIR (set ARTIE_MCP_DIR in .\.env); publication search stays unavailable"
  } else {
    Start-Bg "uv run --directory `"$ARTIE_MCP_DIR`" python -m artie_mcp.server --http" "$env:TEMP\r_artie_mcp.log"
    Wait-Http 'artie-mcp' $ARTIE_MCP_URL $START_TIMEOUT `
      "server binds its MCP_PORT (default 8002); if that mismatches the url in $CONFIG_DIR\mcp_servers.yaml, fix it (see $env:TEMP\r_artie_mcp.log)"
  }

  # 3. r-assist API
  if (Test-Http "http://127.0.0.1:$API_PORT/health") {
    Write-Host 'r-assist: already running'
  } else {
    Start-Bg "uv run uvicorn r_assist.api:app --port $API_PORT" "$env:TEMP\r_assist.log"
    Wait-Http 'r-assist' "http://127.0.0.1:$API_PORT/health" $START_TIMEOUT "see $env:TEMP\r_assist.log"
  }

  Write-Host ''
  if ($procs.Count -eq 0) {
    Write-Host 'all services were already up - nothing started, nothing to stop'
  } else {
    Write-Host 'all services up; Ctrl-C here to stop them'
  }
  Write-Host '  web client: npm run dev in bdc-assist-client, then open http://localhost:5173'
  Write-Host '  or run demo.ipynb'
  if ($procs.Count -gt 0) {
    Wait-Process -Id ($procs | ForEach-Object Id)
  }
} finally {
  foreach ($p in $procs) {
    if (-not $p.HasExited) { taskkill /T /F /PID $p.Id 2>$null | Out-Null }
  }
}
