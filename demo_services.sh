#!/usr/bin/env bash
# Spawn everything demo.ipynb needs:
#   1. Ollama embeddings at EMBEDDING_URL (../bdc-doc-mcp/.env) — reused if already running;
#      tunneled from Sterling when local (needs RENCI VPN); skipped when unset (cloud provider)
#   2. bdc-doc-mcp MCP server (HTTP) on MCP_PORT (../bdc-doc-mcp/.env, default 8001),
#      health-checked at DOC_RAG_MCP_URL (./.env) — the URL bdc-assist actually connects to
#   3. bdc-assist API on :8010 (hardcoded — demo.ipynb hardcodes it too)
# Services already running are left alone; Ctrl-C stops only what this script started.
# Logs: /tmp/bdc_*.log
set -u
cd "$(dirname "$0")"

case "$(uname -r)" in *[Mm]icrosoft*)
  echo "this is WSL bash — run me from Git Bash instead (kubectl, uv, and the token cache live on the Windows side)" >&2
  exit 1;;
esac

env_get() {  # env_get <file> <key> <default> — shell env wins, then the .env, then default
  local v="${!2:-}"
  if [ -z "$v" ] && [ -f "$1" ]; then
    v=$(sed -n "s/^$2=//p" "$1" | tail -1 | sed 's/\r$//;s/[[:space:]]*#.*//;s/^["'\'']//;s/["'\'']$//')
  fi
  echo "${v:-$3}"
}

EMBEDDING_URL=$(env_get ../bdc-doc-mcp/.env EMBEDDING_URL "")
MCP_PORT=$(env_get ../bdc-doc-mcp/.env MCP_PORT 8001)
DOC_RAG_MCP_URL=$(env_get ./.env DOC_RAG_MCP_URL "http://127.0.0.1:8001/mcp")  # config.py default

up() { curl -s -o /dev/null --max-time 2 "$1"; }  # any HTTP response counts, even 4xx

wait_for() {  # wait_for <name> <url> <tries> <hint>
  for _ in $(seq 1 "$3"); do
    up "$2" && { echo "$1: up ($2)"; return 0; }
    sleep 1
  done
  echo "$1: not answering at $2 — $4" >&2
  exit 1
}

pids=()
stop() {
  for pid in "${pids[@]}"; do
    # Git Bash: kill the whole Windows process tree (plain kill orphans uv's children)
    winpid=$(cat "/proc/$pid/winpid" 2>/dev/null)
    if [ -n "$winpid" ]; then taskkill //T //F //PID "$winpid" >/dev/null 2>&1
    else kill "$pid" 2>/dev/null; fi
  done
}
trap stop EXIT

# 1. embeddings
if [ -z "$EMBEDDING_URL" ]; then
  echo "embeddings: EMBEDDING_URL unset — cloud provider, nothing to spawn"
elif up "$EMBEDDING_URL"; then
  echo "ollama: already running ($EMBEDDING_URL)"
elif echo "$EMBEDDING_URL" | grep -qE '://(localhost|127\.0\.0\.1)'; then
  # foreground pre-flight: cluster auth is OIDC (kubelogin) — with a stale token this
  # pops a browser login, which would hang forever inside the backgrounded port-forward
  echo "checking cluster access — if a browser login tab opens (maybe unfocused), complete it; waiting..."
  # no --request-timeout here: it would abort the interactive OIDC login mid-flight
  kubectl -n ner get svc ollama >/dev/null \
    || { echo "kubectl can't reach the cluster — RENCI VPN connected? OIDC login completed?" >&2; exit 1; }
  port=$(echo "$EMBEDDING_URL" | grep -oE ':[0-9]+' | tail -1 | tr -d :)
  kubectl -n ner port-forward svc/ollama "${port:-11434}:11434" >/tmp/bdc_ollama.log 2>&1 &
  pids+=($!)
  wait_for ollama "$EMBEDDING_URL" 15 "see /tmp/bdc_ollama.log"
else
  echo "embeddings at $EMBEDDING_URL not answering — remote URL, can't spawn it from here" >&2
  exit 1
fi

# 2. doc MCP server
if up "$DOC_RAG_MCP_URL"; then
  echo "bdc-doc-mcp: already running ($DOC_RAG_MCP_URL)"
else
  uv run --directory ../bdc-doc-mcp python -m bdc_doc_mcp.mcp_server --http >/tmp/bdc_doc_mcp.log 2>&1 &
  pids+=($!)
  wait_for bdc-doc-mcp "$DOC_RAG_MCP_URL" 30 \
    "server binds MCP_PORT=$MCP_PORT; if that mismatches DOC_RAG_MCP_URL, fix the .env (see /tmp/bdc_doc_mcp.log)"
fi

# 3. bdc-assist API
if up http://127.0.0.1:8010/health; then
  echo "bdc-assist: already running"
else
  uv run uvicorn bdc_assist.api:app --port 8010 >/tmp/bdc_assist.log 2>&1 &
  pids+=($!)
  wait_for bdc-assist http://127.0.0.1:8010/health 30 "see /tmp/bdc_assist.log"
fi

echo
echo "all services up — run demo.ipynb; Ctrl-C here to stop them"
wait
