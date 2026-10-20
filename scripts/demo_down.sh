#!/usr/bin/env bash
# Stop the demo API and apps started by scripts/demo_up.sh (pid files in logs/), including their child processes.
# Only processes it started are stopped; it never kills by port.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for name in api web; do
  f="$ROOT/logs/demo_$name.pid"
  if [ -f "$f" ]; then
    pid="$(cat "$f")"
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    rm -f "$f"
  fi
  l="$ROOT/logs/demo_$name.listen"
  if [ -f "$l" ]; then
    for pid in $(cat "$l"); do pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done
    rm -f "$l"
  fi
done
for i in 1 2 3 4 5 6 7 8; do
  lsof -nP -iTCP:${CORRIDOR_API_PORT:-8000} -iTCP:${CORRIDOR_WEB_PORT:-3000} -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 1
done
[ "${1:-}" = "quiet" ] || echo "demo stopped"
