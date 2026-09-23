#!/bin/bash
# Stop LLM Monitor
if pkill -f "monitor.py"; then
    echo "Stopped"
else
    echo "Not running"
fi
