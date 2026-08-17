#!/bin/bash

set -euo pipefail

# Install omarchy-sesh (user-level, no sudo): binary, both systemd user units,
# and the Hyprland autostart restore hook. Idempotent: safe to rerun.
# Uninstall with ./uninstall.sh.

BIN_SRC="${BIN_SRC:-$(cd "$(dirname "$0")" && pwd)/bin/omarchy-sesh}"
UNIT_DIR_SRC="$(cd "$(dirname "$0")" && pwd)/systemd/user"

BIN="$HOME/.local/bin/omarchy-sesh"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART="$HOME/.config/hypr/autostart.lua"

MARKER_COMMENT="# omarchy-sesh: restore saved windows after login (guard skips if already restored)"
RESTORE_LINE='hl.exec_cmd("sleep 2 && omarchy-sesh restore")'

install -m 755 "$BIN_SRC" "$BIN"
echo "installed $BIN"

install -d "$UNIT_DIR"
cp "$UNIT_DIR_SRC/omarchy-sesh.service" "$UNIT_DIR_SRC/omarchy-sesh-autosave.service" "$UNIT_DIR/"
echo "installed units to $UNIT_DIR"

systemctl --user daemon-reload
systemctl --user enable --now omarchy-sesh.service omarchy-sesh-autosave.service >/dev/null
echo "enabled + started omarchy-sesh.service, omarchy-sesh-autosave.service"

if [[ ! -f "$AUTOSTART" ]]; then
  install -d "$(dirname "$AUTOSTART")"
  cp /usr/share/omarchy/default/hypr/autostart.lua "$AUTOSTART" 2>/dev/null || : >"$AUTOSTART"
fi

if ! grep -qF "$RESTORE_LINE" "$AUTOSTART"; then
  printf '\n%s\n%s\n\n' "$MARKER_COMMENT" "$RESTORE_LINE" >>"$AUTOSTART"
  echo "added restore hook to $AUTOSTART"
else
  echo "autostart hook already present (skipped)"
fi

echo "omarchy-sesh: installed. Restore runs on next login; save on logout and every 60s."