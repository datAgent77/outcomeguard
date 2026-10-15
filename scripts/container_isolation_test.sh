#!/usr/bin/env bash
# Builds the verifier image, starts the isolation topology and asserts, from inside the
# runner container, that the only way out is the egress proxy. Writes the raw probe output
# to docs/integration/runs/container-isolation-<timestamp>.json.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker/verifier-isolation.compose.yml"

if [[ "${REBUILD:-0}" == "1" ]] || ! docker image inspect outcomeguard-verifier:dev >/dev/null 2>&1; then
  docker build -q -t outcomeguard-verifier:dev -f apps/api/Dockerfile.verifier apps/api >/dev/null
fi
$COMPOSE up -d >/dev/null
trap '$COMPOSE down -v >/dev/null 2>&1' EXIT
sleep 3

OUT="docs/integration/runs/container-isolation-$(date -u +%Y%m%dT%H%M%SZ).json"
$COMPOSE exec -T runner python /probe/container_isolation_probe.py > "$OUT"

# The worker (the only service on data + sandbox) can submit a job to the runner server.
$COMPOSE exec -T worker python - <<'PY' > "${OUT%.json}.worker.json"
import httpx, json
job = {"run_id": "srv", "delivery_url": "http://172.31.250.10:8020/landing-page-broken/",
       "proxy_url": "http://proxy:3128", "sinks": {},
       "lighthouse": {"bin": "/app/verifier-tools/node_modules/.bin/lighthouse", "band": 5},
       "criteria": [{"criterion_id": "m", "key": "m", "kind": "mobile_viewport",
                     "spec": {"path": "/", "viewport": {"width": 390, "height": 844},
                              "no_horizontal_overflow": True, "required_visible_selectors": [],
                              "timeout_ms": 20000}}]}
r = httpx.post("http://runner:7000/run", json=job, timeout=120)
out = r.json()
print(json.dumps({"runner_server_status": r.status_code,
                  "measurement": out["measurements"]["m"],
                  "artifacts": [a["label"] for a in out["artifacts"]]}))
PY

python3 - "$OUT" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
must_fail = ["direct_tcp_internet_1.1.1.1_443", "direct_tcp_fixture_172.31.250.10_8020",
             "direct_tcp_internal_svc_8000", "direct_http_example_com",
             "chromium_no_proxy_bypass_fixture", "chromium_no_proxy_bypass_internet"]
must_block = ["proxy_internal_svc", "proxy_metadata", "proxy_gateway_private",
              "proxy_fixture_other_port", "proxy_loopback_of_proxy"]
bad = [k for k in must_fail if r[k]["ok"]]
bad += [k for k in must_block if not (r[k]["ok"] and "blocked=None" not in r[k]["detail"])]
bad += [k for k in ("tcp_proxy_3128_reachable", "proxy_fixture_allowed",
                    "chromium_with_proxy_fixture", "real_verification_job") if not r[k]["ok"]]
job = r["real_verification_job"]["detail"] if r["real_verification_job"]["ok"] else {}
if not (isinstance(job, dict) and job.get("lighthouse_scores")
        and job.get("mobile", {}).get("scroll_width") and job.get("http")):
    bad.append("real_verification_job")  # every tool must have produced a measurement
if not r["proxy_fixture_allowed"]["detail"].startswith("200"):
    bad.append("proxy_fixture_allowed:status")
for k, v in r.items():
    print(f"{'PASS' if k not in bad else 'FAIL'}  {k}: {str(v['detail'])[:150]}")
sys.exit(1 if bad else 0)
PY
echo "worker -> runner server: $(cat "${OUT%.json}.worker.json")" | cut -c1-300
echo "raw output: $OUT"
