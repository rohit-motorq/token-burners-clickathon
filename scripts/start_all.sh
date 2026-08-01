#!/usr/bin/env bash
# Starts both servers in the background, logs to logs/, tracks PIDs so
# stop_all.sh can find them. Re-running restarts anything already running.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p logs

start_one() {
    local name="$1" pidfile="$2"; shift 2
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "$name already running (pid $(cat "$pidfile")), skipping."
        return
    fi
    "$@" > "logs/$name.log" 2>&1 &
    echo $! > "$pidfile"
    echo "$name started, pid $(cat "$pidfile"), log: logs/$name.log"
}

start_one mcp_server logs/mcp_server.pid .venv/bin/python -m src.mcp_server.server
start_one agent_server logs/agent_server.pid .venv/bin/python -m uvicorn src.agent.server:app --host 0.0.0.0 --port 8000

sleep 8
echo
echo "Checking health..."
curl -s http://localhost:8000/health && echo " <- agent server" || echo "agent server not responding yet — check logs/agent_server.log"
curl -s -o /dev/null -w "%{http_code}" http://localhost:8811/sse --max-time 2 && echo " <- mcp server (HEAD /sse)" || true
