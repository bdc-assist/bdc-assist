#!/usr/bin/env bash
# Spawn everything demo.ipynb needs:
#   1. r-doc-mcp MCP server (HTTP) from DOC_MCP_DIR (./.env, default ../r-doc-mcp) on MCP_PORT
#      (that repo's .env, default 8001), health-checked at the r_doc_mcp url in
#      <CONFIG_DIR>/mcp_servers.yaml — the URL r-assist actually connects to
#   2. r-assist API on API_PORT (./.env, default 8010)
# Embedding/LLM endpoints are whatever the two .env files point at; start those yourself.
# Services already running are left alone; Ctrl-C stops only what this script started.
# Each service gets START_TIMEOUT seconds (./.env, default 30) to answer.
# Logs: /tmp/r_*.log
set -u
cd "$(dirname "$0")"

env_get() {  # env_get <file> <key> <default> — shell env wins, then the .env, then default
  local v="${!2:-}"
  if [ -z "$v" ] && [ -f "$1" ]; then
    v=$(sed -n "s/^$2=//p" "$1" | tail -1 | sed 's/\r$//;s/[[:space:]]*#.*//;s/^["'\'']//;s/["'\'']$//')
  fi
  echo "${v:-$3}"
}

DOC_MCP_DIR=$(env_get ./.env DOC_MCP_DIR ../r-doc-mcp)
START_TIMEOUT=$(env_get ./.env START_TIMEOUT 30)
MCP_PORT=$(env_get "$DOC_MCP_DIR/.env" MCP_PORT 8001)
API_PORT=$(env_get ./.env API_PORT 8010)
CONFIG_DIR=$(env_get ./.env CONFIG_DIR config)
mcp_url() {  # mcp_url <yaml> <server> <default> — shell DOC_RAG_MCP_URL wins (test hook), then the yaml block's url:, then default
  local v="${DOC_RAG_MCP_URL:-}"
  if [ -z "$v" ] && [ -f "$1" ]; then
    v=$(sed -n "/^$2:/,/^[^[:space:]#]/s/^[[:space:]]*url:[[:space:]]*//p" "$1" | head -1 | sed 's/\r$//;s/[[:space:]]*#.*//;s/^["'\'']//;s/["'\'']$//')
  fi
  echo "${v:-$3}"
}

DOC_RAG_MCP_URL=$(mcp_url "$CONFIG_DIR/mcp_servers.yaml" r_doc_mcp "http://127.0.0.1:8001/mcp")

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

# 1. doc MCP server
if up "$DOC_RAG_MCP_URL"; then
  echo "r-doc-mcp: already running ($DOC_RAG_MCP_URL)"
else
  uv run --directory "$DOC_MCP_DIR" python -m r_doc_mcp.mcp_server --http >/tmp/r_doc_mcp.log 2>&1 &
  pids+=($!)
  wait_for r-doc-mcp "$DOC_RAG_MCP_URL" "$START_TIMEOUT" \
    "server binds MCP_PORT=$MCP_PORT; if that mismatches the url in $CONFIG_DIR/mcp_servers.yaml, fix it (see /tmp/r_doc_mcp.log)"
fi

# 2. r-assist API
if up "http://127.0.0.1:$API_PORT/health"; then
  echo "r-assist: already running"
else
  uv run uvicorn r_assist.api:app --port "$API_PORT" >/tmp/r_assist.log 2>&1 &
  pids+=($!)
  wait_for r-assist "http://127.0.0.1:$API_PORT/health" "$START_TIMEOUT" "see /tmp/r_assist.log"
fi

echo
echo "all services up — run demo.ipynb; Ctrl-C here to stop them"
wait
