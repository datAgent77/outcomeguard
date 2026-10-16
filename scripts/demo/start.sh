#!/usr/bin/env bash
# One-command local demo stack. Default PAYMENT_MODE=rehearsal: every new payment is
# SIMULATED (no PayPal call). Use PAYMENT_MODE=sandbox only for a human-approved Sandbox run.
#   scripts/demo/start.sh            # API :8010, worker, fixtures :8020, web :3010
#   scripts/demo/stop.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN=/tmp/og-demo
MODE="${PAYMENT_MODE:-rehearsal}"
mkdir -p "$RUN"
cd "$ROOT"
[ -f .env ] || { echo "missing .env (copy .env.example)"; exit 1; }
for port in 8010 8020 3010; do
  if lsof -iTCP:$port -sTCP:LISTEN -P >/dev/null 2>&1; then echo "port $port is in use — run scripts/demo/stop.sh or free it"; exit 1; fi
done

echo "▶ fixtures :8020 (broken / fixed landing pages)"
(
  cd fixtures || exit 1
  nohup python3 serve.py --port 8020 --sink-url http://127.0.0.1:8010/sinks/default </dev/null >"$RUN/fixtures.log" 2>&1 &
  echo $! >"$RUN/fixtures.pid"
)

echo "▶ API :8010 (PAYMENT_MODE=$MODE)"
(
  cd apps/api || exit 1
  PAYMENT_MODE="$MODE" PUBLIC_API_URL=http://localhost:8010 WEB_URL=http://localhost:3010 \
    nohup uv run uvicorn outcomeguard.main:app --port 8010 </dev/null >"$RUN/api.log" 2>&1 &
  echo $! >"$RUN/api.pid"
)

echo "▶ verification worker (no PayPal / AI keys in its environment)"
(
  cd apps/api || exit 1
  env -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY -u PAYPAL_CLIENT_ID -u PAYPAL_CLIENT_SECRET \
    nohup uv run python -m outcomeguard.verification.worker </dev/null >"$RUN/worker.log" 2>&1 &
  echo $! >"$RUN/worker.pid"
)

echo "▶ web :3010 (production build)"
if [ ! -d apps/web/.next ]; then (cd apps/web && npm run build >"$RUN/build.log" 2>&1); fi
(
  cd apps/web || exit 1
  OUTCOMEGUARD_API_URL=http://localhost:8010 nohup npx next start -p 3010 </dev/null >"$RUN/web.log" 2>&1 &
  echo $! >"$RUN/web.pid"
)

for i in $(seq 1 60); do
  if curl -fsS localhost:8010/ready >/dev/null 2>&1 && curl -fsS -o /dev/null localhost:3010/login 2>/dev/null; then break; fi
  sleep 1
done
curl -fsS localhost:8010/ready | python3 -c 'import json,sys; c=json.load(sys.stdin)["config"]; print("✔ API ready · payment mode:", c["payment_mode"], "· AI:", c.get("ai_model"))'
curl -fsS -o /dev/null -w "✔ web %{http_code} http://localhost:3010\n" localhost:3010/login
curl -fsS -o /dev/null -w "✔ fixtures %{http_code}\n" http://127.0.0.1:8020/landing-page-fixed/
echo "Next: DEMO_PASSWORD=… uv run --directory apps/api python scripts/prepare_demo.py"
