#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

stop_one() {
    local name="$1" pidfile="$2"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        kill "$(cat "$pidfile")"
        echo "$name stopped (was pid $(cat "$pidfile"))"
    else
        echo "$name not running"
    fi
    rm -f "$pidfile"
}

stop_one mcp_server logs/mcp_server.pid
stop_one agent_server logs/agent_server.pid
