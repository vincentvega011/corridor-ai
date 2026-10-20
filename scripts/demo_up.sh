#!/usr/bin/env bash
# One command for both demos (housing scam, tuition): migrate, reset the sandbox, start the API with the demo clock
# (10 Oct 2026, 09:00 Nepal time) and the customer app plus bank console on a production build.
#   make demo                 offline: model calls come from the recorded cassettes (LLM_REPLAY=1), nothing leaves the machine
#   DEMO_LLM=live make demo   real model servers (run `make models-up` first, one local model at a time)
# Logs: logs/demo_api.log, logs/demo_web.log. Stop with `make demo-down`. Idempotent: a running demo is restarted.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p logs
API_PORT="${CORRIDOR_API_PORT:-8000}"   # override to run beside another copy: CORRIDOR_API_PORT=8200 CORRIDOR_WEB_PORT=3200 make demo
WEB_PORT="${CORRIDOR_WEB_PORT:-3000}"
export CORRIDOR_API_PORT="$API_PORT" CORRIDOR_WEB_PORT="$WEB_PORT"
T0=$(date +%s)
step() { printf '%s  %s\n' "$(( $(date +%s) - T0 ))s" "$*"; }

./scripts/demo_down.sh quiet
for port in $API_PORT $WEB_PORT; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $port is already in use by something this script did not start; stop it first"; exit 1
  fi
done
step "migrate and reset the sandbox"
(cd backend && APP_ENV=demo uv run alembic upgrade head >"$ROOT/logs/demo_migrate.log" 2>&1 \
  && APP_ENV=demo uv run python -m corridor.sandbox.demo_reset >"$ROOT/logs/demo_reset.log" 2>&1)

# never serve a stale build: rebuild when any source, message file or config is newer than the build, or the build is missing
# (a plain production build, for example from make web-check, has no persona switcher: only a build with the DEMO_BUILD marker is reused)
if [ ! -f frontend/.next/BUILD_ID ] || [ ! -f frontend/.next/DEMO_BUILD ] || [ -n "$(find frontend/app frontend/components frontend/lib frontend/messages frontend/proxy.ts frontend/next.config.ts frontend/package.json -newer frontend/.next/BUILD_ID -type f 2>/dev/null | head -1)" ]; then
  step "build the apps (source changed or no build; production build with the demo persona switcher)"
  (cd frontend && NEXT_PUBLIC_DEMO_PERSONAS=1 pnpm exec next build >"$ROOT/logs/demo_build.log" 2>&1 && touch .next/DEMO_BUILD) || { echo "the frontend build failed, see logs/demo_build.log"; exit 1; }
fi

REPLAY=1
[ "${DEMO_LLM:-replay}" = "live" ] && REPLAY=0
step "start the API (demo clock, LLM_REPLAY=$REPLAY)"
(cd backend && APP_ENV=demo DEMO_CLOCK=on LLM_REPLAY=$REPLAY FRONTEND_ORIGIN=http://localhost:$WEB_PORT nohup uv run uvicorn corridor.api.main:app --host 127.0.0.1 --port $API_PORT \
  >"$ROOT/logs/demo_api.log" 2>&1 & echo $! >"$ROOT/logs/demo_api.pid")
step "start the apps"
(cd frontend && BACKEND_URL=http://127.0.0.1:$API_PORT nohup pnpm exec next start -p $WEB_PORT >"$ROOT/logs/demo_web.log" 2>&1 & echo $! >"$ROOT/logs/demo_web.pid")

for i in $(seq 1 60); do
  curl -fs http://127.0.0.1:$API_PORT/healthz >/dev/null 2>&1 && curl -fs http://127.0.0.1:$WEB_PORT/manifest.webmanifest >/dev/null 2>&1 && break
  sleep 1
done
curl -fs http://127.0.0.1:$API_PORT/healthz >/dev/null || { echo "API did not start, see logs/demo_api.log"; exit 1; }
curl -fs http://127.0.0.1:$WEB_PORT/manifest.webmanifest >/dev/null || { echo "apps did not start, see logs/demo_web.log"; exit 1; }
# remember the processes that actually listen (the wrappers started above are not them), so demo-down stops exactly these
lsof -nP -iTCP:$API_PORT -sTCP:LISTEN -t >"$ROOT/logs/demo_api.listen" 2>/dev/null || true
lsof -nP -iTCP:$WEB_PORT -sTCP:LISTEN -t >"$ROOT/logs/demo_web.listen" 2>/dev/null || true
# give the bank console something to show (the genuine landlord's verification request, the housing scam proposals,
# a refused button payment), through the running API like a customer would; a failure only means an emptier console
if [ "$REPLAY" = "1" ]; then
  step "seed the console queues"
  (cd backend && APP_ENV=demo uv run python -m corridor.sandbox.demo_queues --api "http://127.0.0.1:$API_PORT" >"$ROOT/logs/demo_queues.log" 2>&1) \
    || echo "console queues not seeded, see logs/demo_queues.log"
fi
step "ready"
cat <<MSG
  Customer app   http://127.0.0.1:$WEB_PORT/           (persona switcher: Priya, Ramesh, Anil; en and ne)
  Verifier page  http://127.0.0.1:$WEB_PORT/verify
  Bank console   http://127.0.0.1:$WEB_PORT/console    (officer_asha or officer_binod, tokens in secrets/officer_tokens.json)
  Demo clock     10 Oct 2026, 09:00 Nepal time; reset between takes: make demo-reset; stop: make demo-down
MSG
