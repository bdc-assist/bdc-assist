#!/usr/bin/env bash
# Spawn everything demo.ipynb and the web client (bdc-assist-client) need:
#   1. r-doc-mcp MCP server (HTTP) from DOC_MCP_DIR (./.env, default ../bdc-doc-mcp) on MCP_PORT
#      (that repo's .env, default 8001), health-checked at the r_doc_mcp url in
#      <CONFIG_DIR>/mcp_servers.yaml — the URL r-assist actually connects to
#   2. artie-mcp MCP server (HTTP) from ARTIE_MCP_DIR (./.env, default ../artie-mcp), only when
#      <CONFIG_DIR>/mcp_servers.yaml has an artie_mcp block (examples/bdc), health-checked at its url
#   3. bdc-side-panel (the web client's form side panel) from SIDE_PANEL_DIR (./.env, default
#      ../bdc-side-panel) on SIDE_PANEL_PORT (that repo's .env, default 8012), skipped when there is no checkout
#   4. r-assist API on API_PORT (./.env, default 8010)
# Embedding/LLM endpoints are whatever the .env files point at; start those yourself.
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

DOC_MCP_DIR=$(env_get ./.env DOC_MCP_DIR ../bdc-doc-mcp)
ARTIE_MCP_DIR=$(env_get ./.env ARTIE_MCP_DIR ../artie-mcp)
SIDE_PANEL_DIR=$(env_get ./.env SIDE_PANEL_DIR ../bdc-side-panel)
START_TIMEOUT=$(env_get ./.env START_TIMEOUT 30)
MCP_PORT=$(env_get "$DOC_MCP_DIR/.env" MCP_PORT 8001)
SIDE_PANEL_PORT=$(env_get "$SIDE_PANEL_DIR/.env" SIDE_PANEL_PORT 8012)
API_PORT=$(env_get ./.env API_PORT 8010)
CONFIG_DIR=$(env_get ./.env CONFIG_DIR config)
mcp_url() {  # mcp_url <yaml> <server> <default> — the yaml block's url:, then default
  local v=""
  if [ -f "$1" ]; then
    v=$(sed -n "/^$2:/,/^[^[:space:]#]/s/^[[:space:]]*url:[[:space:]]*//p" "$1" | head -1 | sed 's/\r$//;s/[[:space:]]*#.*//;s/^["'\'']//;s/["'\'']$//')
  fi
  echo "${v:-$3}"
}

DOC_RAG_MCP_URL=${DOC_RAG_MCP_URL:-$(mcp_url "$CONFIG_DIR/mcp_servers.yaml" r_doc_mcp "http://127.0.0.1:8001/mcp")}  # shell wins: test hook
ARTIE_MCP_URL=$(mcp_url "$CONFIG_DIR/mcp_servers.yaml" artie_mcp "")  # empty: this config doesn't use artie-mcp

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

# 2. artie-mcp, if the config uses it
if [ -z "$ARTIE_MCP_URL" ]; then
  :
elif up "$ARTIE_MCP_URL"; then
  echo "artie-mcp: already running ($ARTIE_MCP_URL)"
elif [ ! -d "$ARTIE_MCP_DIR" ]; then
  echo "artie-mcp: no checkout at $ARTIE_MCP_DIR (set ARTIE_MCP_DIR in ./.env); publication search stays unavailable" >&2
else
  uv run --directory "$ARTIE_MCP_DIR" python -m artie_mcp.server --http >/tmp/r_artie_mcp.log 2>&1 &
  pids+=($!)
  wait_for artie-mcp "$ARTIE_MCP_URL" "$START_TIMEOUT"     "server binds its MCP_PORT (default 8002); if that mismatches the url in $CONFIG_DIR/mcp_servers.yaml, fix it (see /tmp/r_artie_mcp.log)"
fi

# 3. bdc-side-panel, if checked out
SIDE_PANEL_URL="http://127.0.0.1:$SIDE_PANEL_PORT"
side_panel=""
if up "$SIDE_PANEL_URL/health"; then
  echo "bdc-side-panel: already running ($SIDE_PANEL_URL)"
  side_panel=1
elif [ ! -d "$SIDE_PANEL_DIR" ]; then
  echo "bdc-side-panel: no checkout at $SIDE_PANEL_DIR (set SIDE_PANEL_DIR in ./.env); the client's form side panel stays off"
else
  uv run --directory "$SIDE_PANEL_DIR" python -m side_panel.api >/tmp/r_side_panel.log 2>&1 &
  pids+=($!)
  wait_for bdc-side-panel "$SIDE_PANEL_URL/health" "$START_TIMEOUT" \
    "server binds its SIDE_PANEL_PORT (default 8012); if that mismatches $SIDE_PANEL_PORT, fix its .env (see /tmp/r_side_panel.log)"
  side_panel=1
fi

# 4. r-assist API
if up "http://127.0.0.1:$API_PORT/health"; then
  echo "r-assist: already running"
else
  uv run uvicorn r_assist.api:app --port "$API_PORT" >/tmp/r_assist.log 2>&1 &
  pids+=($!)
  wait_for r-assist "http://127.0.0.1:$API_PORT/health" "$START_TIMEOUT" "see /tmp/r_assist.log"
fi

echo
echo "all services up; Ctrl-C here to stop them"
if [ -n "$side_panel" ]; then
  echo "  web client: VITE_SIDE_PANEL_URL=$SIDE_PANEL_URL npm run dev in bdc-assist-client, then open http://localhost:5173"
else
  echo "  web client: npm run dev in bdc-assist-client, then open http://localhost:5173"
fi
echo "  or run demo.ipynb"
wait
