#!/usr/bin/env bash
# Installs tahweel for the current user:
#   ~/.local/bin/tahweel        command (no arguments opens the window)
#   app menu entry + icon       "Tahweel / تحويل", also listed under "Open With" for PDF, Word and Markdown
# Python packages go into .venv inside this folder; nothing is installed system-wide.
# Undo with ./uninstall.sh
#
# Usage: ./install.sh

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin/tahweel"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
APP_ID="io.github.ahmad.Tahweel"

command -v python3 >/dev/null || { echo "Missing dependency: python3" >&2; exit 1; }
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null \
    || echo "Note: GTK4/libadwaita Python bindings not found; the window won't open, the command line still works." >&2

if [ ! -x "$SRC/.venv/bin/python" ]; then
    echo "Creating virtual environment…"
    python3 -m venv --system-site-packages "$SRC/.venv"   # system site: PyGObject comes from the distro
fi
"$SRC/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$SRC/requirements.txt"

mkdir -p "$(dirname "$BIN")" "$APPS" "$ICONS"
cat > "$BIN" <<LAUNCHER
#!/bin/sh
# tahweel launcher (installed by $SRC/install.sh)
PYTHONPATH="$SRC\${PYTHONPATH:+:\$PYTHONPATH}" exec "$SRC/.venv/bin/python" -m tahweel "\$@"
LAUNCHER
chmod 755 "$BIN"

install -m 644 "$SRC/data/$APP_ID.svg" "$ICONS/$APP_ID.svg"
sed "s|@BIN@|$BIN|" "$SRC/data/$APP_ID.desktop.in" > "$APPS/$APP_ID.desktop"
update-desktop-database "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Installed. Run 'tahweel' for the window, or 'tahweel file.pdf' / 'tahweel notes.md -t pdf' in a terminal."
command -v soffice >/dev/null || echo "Note: LibreOffice (soffice) not found; converting *to* PDF will not work." >&2
