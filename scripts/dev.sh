#!/usr/bin/env bash
# Start / restart the app in demo or real mode. Kills whatever is already on
# the ports first, so it's always safe to re-run.
#
#   ./scripts/dev.sh demo             # demo backend (:8000) + frontend (:5173)
#   ./scripts/dev.sh real             # real backend  + frontend
#   ./scripts/dev.sh demo --api-only  # backend only (skip Vite)
#   ./scripts/dev.sh stop             # stop both
#
# Logs: /tmp/journal-backend.log, /tmp/journal-frontend.log
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
API_PORT=8000
WEB_PORT=5173
BACKEND_LOG=/tmp/journal-backend.log
FRONTEND_LOG=/tmp/journal-frontend.log

kill_port() {  # kill whatever holds the port, then wait for it to actually free
  local pids
  pids=$(lsof -ti :"$1" 2>/dev/null || true)
  [ -n "$pids" ] || return 0
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 20); do
    lsof -ti :"$1" >/dev/null 2>&1 || return 0
    sleep 0.3
  done
}

wait_for() {  # wait_for <url> <name>
  for _ in $(seq 1 30); do
    curl -sf "$1" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "✗ $2 did not come up — check its log" >&2
  return 1
}

mode="${1:-}"

if [ "$mode" = "stop" ]; then
  kill_port "$API_PORT"; kill_port "$WEB_PORT"
  echo "Stopped backend (:$API_PORT) and frontend (:$WEB_PORT)."
  exit 0
fi

if [ "$mode" != "demo" ] && [ "$mode" != "real" ]; then
  echo "usage: $0 {demo|real|stop} [--api-only]" >&2
  exit 1
fi

# --- backend ---
kill_port "$API_PORT"
( cd "$BACKEND" && APP_MODE="$mode" .venv/bin/uvicorn main:app --port "$API_PORT" \
    >"$BACKEND_LOG" 2>&1 & )
wait_for "http://localhost:$API_PORT/trends" "backend"
grep -m1 "APP_MODE=" "$BACKEND_LOG" || true
echo "✓ backend ($mode) → http://localhost:$API_PORT   log: $BACKEND_LOG"

# --- frontend ---
if [ "${2:-}" = "--api-only" ]; then
  echo "(skipping frontend)"
  exit 0
fi
kill_port "$WEB_PORT"
( cd "$FRONTEND" && npm run dev >"$FRONTEND_LOG" 2>&1 & )
wait_for "http://localhost:$WEB_PORT" "frontend"
echo "✓ frontend → http://localhost:$WEB_PORT   log: $FRONTEND_LOG"
