# Native Windows twin of demo_services.sh - same behavior, keep the two in sync.
# Spawns everything demo.ipynb needs:
#   1. Ollama embeddings at EMBEDDING_URL (..\bdc-doc-mcp\.env) - reused if already running;
#      tunneled from Sterling when local (needs RENCI VPN); skipped when unset (cloud provider)
#   2. bdc-doc-mcp MCP server (HTTP) on MCP_PORT (..\bdc-doc-mcp\.env, default 8001),
#      health-checked at DOC_RAG_MCP_URL (.\.env) - the URL bdc-assist actually connects to
#   3. bdc-assist API on :8010 (hardcoded - demo.ipynb hardcodes it too)
# Services already running are left alone; Ctrl-C stops only what this script started.
# Logs: %TEMP%\bdc_*.log
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

$EMBEDDING_URL   = Get-DotEnv '..\bdc-doc-mcp\.env' 'EMBEDDING_URL' ''
$MCP_PORT        = Get-DotEnv '..\bdc-doc-mcp\.env' 'MCP_PORT' '8001'
$DOC_RAG_MCP_URL = Get-DotEnv '.\.env' 'DOC_RAG_MCP_URL' 'http://127.0.0.1:8001/mcp'  # config.py default

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
  # 1. embeddings
  if (-not $EMBEDDING_URL) {
    Write-Host 'embeddings: EMBEDDING_URL unset - cloud provider, nothing to spawn'
  } elseif (Test-Http $EMBEDDING_URL) {
    Write-Host "ollama: already running ($EMBEDDING_URL)"
  } elseif ($EMBEDDING_URL -match '://(localhost|127\.0\.0\.1)') {
    if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
      Write-Host 'kubectl not found - install it, or start a local Ollama at EMBEDDING_URL'
      exit 1
    }
    # foreground pre-flight: cluster auth is OIDC (kubelogin) - with a stale token this
    # pops a browser login, which would hang forever inside the backgrounded port-forward
    Write-Host 'checking cluster access - if a browser login tab opens (maybe unfocused), complete it; waiting...'
    & kubectl -n ner get svc ollama | Out-Null
    if ($LASTEXITCODE -ne 0) {
      Write-Host 'kubectl cannot reach the cluster - RENCI VPN connected? OIDC login completed?'
      exit 1
    }
    $tunnelPort = if ($EMBEDDING_URL -match ':(\d+)') { $Matches[1] } else { '11434' }
    Start-Bg "kubectl -n ner port-forward svc/ollama ${tunnelPort}:11434" "$env:TEMP\bdc_ollama.log"
    Wait-Http 'ollama' $EMBEDDING_URL 15 "see $env:TEMP\bdc_ollama.log"
  } else {
    Write-Host "embeddings at $EMBEDDING_URL not answering - remote URL, can't spawn it from here"
    exit 1
  }

  # 2. doc MCP server
  if (Test-Http $DOC_RAG_MCP_URL) {
    Write-Host "bdc-doc-mcp: already running ($DOC_RAG_MCP_URL)"
  } else {
    Start-Bg 'uv run --directory ..\bdc-doc-mcp python -m bdc_doc_mcp.mcp_server --http' "$env:TEMP\bdc_doc_mcp.log"
    Wait-Http 'bdc-doc-mcp' $DOC_RAG_MCP_URL 30 `
      "server binds MCP_PORT=$MCP_PORT; if that mismatches DOC_RAG_MCP_URL, fix the .env (see $env:TEMP\bdc_doc_mcp.log)"
  }

  # 3. bdc-assist API
  if (Test-Http 'http://127.0.0.1:8010/health') {
    Write-Host 'bdc-assist: already running'
  } else {
    Start-Bg 'uv run uvicorn bdc_assist.api:app --port 8010' "$env:TEMP\bdc_assist.log"
    Wait-Http 'bdc-assist' 'http://127.0.0.1:8010/health' 30 "see $env:TEMP\bdc_assist.log"
  }

  Write-Host ''
  if ($procs.Count -eq 0) {
    Write-Host 'all services were already up - nothing started, nothing to stop'
  } else {
    Write-Host 'all services up - run demo.ipynb; Ctrl-C here to stop them'
    Wait-Process -Id ($procs | ForEach-Object Id)
  }
} finally {
  foreach ($p in $procs) {
    if (-not $p.HasExited) { taskkill /T /F /PID $p.Id 2>$null | Out-Null }
  }
}
