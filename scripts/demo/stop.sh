#!/usr/bin/env bash
# Stops the demo stack started by scripts/demo/start.sh (matched by its exact commands).
RUN=/tmp/og-demo
pkill -f "next start -p 3010" && echo "stopped web"
pkill -f "outcomeguard.verification.worker" && echo "stopped worker"
pkill -f "uvicorn outcomeguard.main:app --port 8010" && echo "stopped api"
pkill -f "serve.py --port 8020" && echo "stopped fixtures"
rm -f "$RUN"/*.pid
exit 0
