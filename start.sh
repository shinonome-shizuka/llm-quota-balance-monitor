#!/usr/bin/env bash
set -euo pipefail

SERVICE="llm-quota-monitor.service"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

if systemctl --user cat "$SERVICE" >/dev/null 2>&1; then
    systemctl --user start "$SERVICE"
    echo "Started $SERVICE"
    exit 0
fi

if pgrep -u "$USER" -f "$PROJECT_DIR/monitor.py (--daemon)?$" >/dev/null; then
    echo "Already running"
    exit 0
fi

cd "$PROJECT_DIR"
if [[ "$(uname -s)" == "Darwin" ]]; then
    nohup uv run monitor.py >monitor.log 2>&1 &
else
    nohup /usr/bin/python3 monitor.py --daemon >monitor.log 2>&1 &
fi
echo "Started (PID: $!)"
