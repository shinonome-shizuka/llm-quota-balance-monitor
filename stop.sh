#!/usr/bin/env bash
set -euo pipefail

SERVICE="llm-quota-monitor.service"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

if systemctl --user cat "$SERVICE" >/dev/null 2>&1; then
    systemctl --user stop "$SERVICE"
    echo "Stopped $SERVICE"
elif pkill -u "$USER" -f "$PROJECT_DIR/monitor.py (--daemon)?$"; then
    echo "Stopped"
else
    echo "Not running"
fi
