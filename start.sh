#!/bin/bash
# Start LLM Monitor in background
if pgrep -f "monitor.py" > /dev/null; then
    echo "Already running"
    exit 0
fi
cd "$(dirname "$0")"
nohup uv run monitor.py > monitor.log 2>&1 &
echo "Started (PID: $!)"
