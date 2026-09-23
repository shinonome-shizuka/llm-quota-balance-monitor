#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXPECTED_DIR="$HOME/Projects/llm-quota-balance-monitor"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/shinonome.llm-quota"
UNIT_DIR="$HOME/.config/systemd/user"

if [[ "$PROJECT_DIR" != "$EXPECTED_DIR" ]]; then
    echo "Install this repository at $EXPECTED_DIR first." >&2
    exit 1
fi

install -d "$PLUGIN_DIR" "$UNIT_DIR"
install -m 0644 "$PROJECT_DIR/omarchy-plugin/manifest.json" "$PLUGIN_DIR/manifest.json"
install -m 0644 "$PROJECT_DIR/omarchy-plugin/BarWidget.qml" "$PLUGIN_DIR/BarWidget.qml"
install -m 0644 "$PROJECT_DIR/systemd/llm-quota-monitor.service" "$UNIT_DIR/llm-quota-monitor.service"

systemctl --user daemon-reload
systemctl --user enable --now llm-quota-monitor.service

echo "Installed shinonome.llm-quota and started llm-quota-monitor.service."
echo "Add shinonome.llm-quota to ~/.config/omarchy/shell.json if it is not already in the bar."
