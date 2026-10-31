#!/usr/bin/env bash
# Removes what install.sh added. The project folder (and its .venv) stays.
set -euo pipefail
APP_ID="io.github.ahmad.Tahweel"
rm -f "$HOME/.local/bin/tahweel" \
      "$HOME/.local/share/applications/$APP_ID.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Uninstalled. Settings (if any) are in ~/.config/tahweel."
