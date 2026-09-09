#!/usr/bin/env bash
# Self-check for demo_services.sh and its demo_services.cmd wrapper — hermetic:
# curl/kubectl/uv/sleep are PATH stubs, the scripts run from a temp dir, and the
# .cmd runs a stub .sh — no real .env, service, or VPN is touched.
# Run: bash tests/test_demo_services.sh
set -eu
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/demo_services.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# --- env_get parsing: test the definition actually in the script, not a copy ---
sed -n '/^env_get()/,/^}/p' "$SCRIPT" > "$TMP/env_get.sh"
. "$TMP/env_get.sh"
printf 'MCP_PORT=9001   # comment\r\nEMBEDDING_URL="http://localhost:11434"\r\nEMPTY=\n' > "$TMP/fake.env"
[ "$(env_get "$TMP/fake.env" MCP_PORT 8001)" = "9001" ] || { echo "FAIL: comment/CRLF strip"; exit 1; }
[ "$(env_get "$TMP/fake.env" EMBEDDING_URL x)" = "http://localhost:11434" ] || { echo "FAIL: quote strip"; exit 1; }
[ "$(env_get "$TMP/fake.env" MISSING default)" = "default" ] || { echo "FAIL: default"; exit 1; }
[ "$(env_get "$TMP/fake.env" EMPTY fallback)" = "fallback" ] || { echo "FAIL: empty -> fallback"; exit 1; }
MCP_PORT=7777
[ "$(env_get "$TMP/fake.env" MCP_PORT 8001)" = "7777" ] || { echo "FAIL: shell env should win"; exit 1; }
unset MCP_PORT
echo "PASS 1/6: env_get parses .env values (comments, CRLF, quotes, precedence)"

# --- orchestration branches, against the real script with stubbed commands ---
mkdir -p "$TMP/repo" "$TMP/bin"
cp "$SCRIPT" "$TMP/repo/"   # ../bdc-doc-mcp/.env doesn't exist here => defaults + shell env only
for c in kubectl uv; do
  printf '#!/bin/sh\necho "%s $*" >> "%s/spawned.log"\n' "$c" "$TMP" > "$TMP/bin/$c"
done
printf '#!/bin/sh\nexit 0\n' > "$TMP/bin/sleep"      # no-op: wait_for loops finish instantly
printf '#!/bin/sh\nexit 0\n' > "$TMP/bin/taskkill"
chmod +x "$TMP/bin/"*

run() {  # run <curl-exit-code> [env...] — returns script's exit code, output in $TMP/out
  local curl_rc=$1; shift
  printf '#!/bin/sh\nexit %s\n' "$curl_rc" > "$TMP/bin/curl"
  chmod +x "$TMP/bin/curl"
  : > "$TMP/spawned.log"
  env PATH="$TMP/bin:$PATH" "$@" bash "$TMP/repo/demo_services.sh" > "$TMP/out" 2>&1 && rc=0 || rc=$?
  return $rc
}

# everything already answering => reuse all three, spawn nothing, exit 0
run 0 EMBEDDING_URL=http://localhost:11434 || { echo "FAIL: all-up should exit 0"; cat "$TMP/out"; exit 1; }
[ "$(grep -c 'already running' "$TMP/out")" = 3 ] || { echo "FAIL: expected 3 reuses"; cat "$TMP/out"; exit 1; }
[ ! -s "$TMP/spawned.log" ] || { echo "FAIL: spawned despite everything up"; cat "$TMP/spawned.log"; exit 1; }
echo "PASS 2/6: services already up -> all reused, nothing spawned"

# nothing answering + local EMBEDDING_URL => OIDC pre-flight, tunnel with the port
# parsed from the URL, then fail loud
run 1 EMBEDDING_URL=http://localhost:12345 && { echo "FAIL: dead ollama should exit non-zero"; exit 1; }
grep -q 'kubectl -n ner get svc ollama' "$TMP/spawned.log" \
  || { echo "FAIL: missing foreground OIDC pre-flight"; cat "$TMP/spawned.log"; exit 1; }
grep -q 'kubectl -n ner port-forward svc/ollama 12345:11434' "$TMP/spawned.log" \
  || { echo "FAIL: tunnel port not parsed from EMBEDDING_URL"; cat "$TMP/spawned.log"; exit 1; }
grep -q 'bdc_ollama.log' "$TMP/out" || { echo "FAIL: missing tunnel-log hint"; cat "$TMP/out"; exit 1; }
echo "PASS 3/6: local ollama down -> pre-flight + tunnel with right port, exits with log hint (as intended)"

# unset EMBEDDING_URL => cloud provider: skip step 1, still start the other two
run 1 EMBEDDING_URL= DOC_RAG_MCP_URL=http://127.0.0.1:8001/mcp && { echo "FAIL: expected mcp wait to fail"; exit 1; }
grep -q 'cloud provider' "$TMP/out" || { echo "FAIL: missing cloud-provider skip"; cat "$TMP/out"; exit 1; }
grep -q 'uv run --directory ../bdc-doc-mcp' "$TMP/spawned.log" \
  || { echo "FAIL: mcp server not spawned"; cat "$TMP/spawned.log"; exit 1; }
grep -qv kubectl "$TMP/spawned.log" || { echo "FAIL: tunneled without EMBEDDING_URL"; exit 1; }
echo "PASS 4/6: EMBEDDING_URL unset -> tunnel skipped (cloud provider), other services still start"

# dead remote EMBEDDING_URL => refuse instead of tunneling somebody else's host
run 1 EMBEDDING_URL=http://sterling:11434 && { echo "FAIL: dead remote should exit non-zero"; exit 1; }
grep -q "can't spawn it from here" "$TMP/out" || { echo "FAIL: missing remote-URL message"; cat "$TMP/out"; exit 1; }
[ ! -s "$TMP/spawned.log" ] || { echo "FAIL: spawned for a remote URL"; cat "$TMP/spawned.log"; exit 1; }
echo "PASS 5/6: remote embedding URL down -> declines to spawn, clear error (as intended)"

# --- native .ps1: run under a clean-PowerShell PATH (no Git dirs), with throwaway
# HTTP listeners standing in for the three services => all reused, nothing spawned,
# exit 0. The copy lives in $TMP/repo so no real .env is read. ---
if ! command -v powershell >/dev/null; then
  echo "SKIP 6/6: not on Windows, demo_services.ps1 untested"
else
  cp "$(dirname "$SCRIPT")/demo_services.ps1" "$TMP/repo/"
  python -m http.server 18131 --bind 127.0.0.1 >/dev/null 2>&1 & L1=$!
  python -m http.server 18132 --bind 127.0.0.1 >/dev/null 2>&1 & L2=$!
  python -m http.server 8010 --bind 127.0.0.1 >/dev/null 2>&1 & L3=$!  # bind may fail if 8010 is busy — busy means something answers, which is all we need
  sleep 2
  winps1=$(cygpath -w "$TMP/repo/demo_services.ps1")
  out=$(env PATH="/c/Windows/System32:/c/Windows:/c/Windows/System32/WindowsPowerShell/v1.0" \
        EMBEDDING_URL=http://127.0.0.1:18131 DOC_RAG_MCP_URL=http://127.0.0.1:18132/mcp \
        powershell -NoProfile -ExecutionPolicy Bypass -File "$winps1" 2>&1) && rc=0 || rc=$?
  kill $L1 $L2 $L3 2>/dev/null
  [ "$rc" = 0 ] || { echo "FAIL: native all-up should exit 0 (got $rc)"; echo "$out"; exit 1; }
  [ "$(echo "$out" | grep -c 'already running')" = 3 ] || { echo "FAIL: expected 3 reuses"; echo "$out"; exit 1; }
  echo "$out" | grep -q "nothing started" || { echo "FAIL: should exit instead of waiting when nothing spawned"; echo "$out"; exit 1; }
  echo "PASS 6/6: native .ps1 (clean-PowerShell PATH) -> .env-style overrides honored, services reused, clean exit"
fi

echo
echo "all 6 checks passed (3 and 5 verify the script fails *correctly* in bad conditions)"
