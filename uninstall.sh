#!/bin/bash

set -euo pipefail

# Remove all traces of omarchy-sesh from the user's system.
# Reverses bin/omarchy-sesh install, the systemd user unit, the Hyprland
# autostart hook, the session DB, config, and logs. Idempotent: safe to rerun.

BIN="$HOME/.local/bin/omarchy-sesh"
UNIT="$HOME/.config/systemd/user/omarchy-sesh.service"
UNIT_WANTS="$HOME/.config/systemd/user/graphical-session.target.wants/omarchy-sesh.service"
AUTOSAVE_UNIT="$HOME/.config/systemd/user/omarchy-sesh-autosave.service"
AUTOSAVE_WANTS="$HOME/.config/systemd/user/graphical-session.target.wants/omarchy-sesh-autosave.service"
AUTOSTART="$HOME/.config/hypr/autostart.lua"
DB="$HOME/.local/state/omarchy/session.db"
LOG_DIR="$HOME/.local/state/omarchy/log"
CONFIG_DIR="$HOME/.config/omarchy/sesh"

AUTOSTART_LINE='omarchy-sesh restore'

removed=0

for link in "$UNIT_WANTS" "$AUTOSAVE_WANTS"; do
  if [[ -L "$link" || -f "$link" ]]; then
    rm -f "$link"
    removed=1
  fi
done

if [[ -f "$UNIT" || -f "$AUTOSAVE_UNIT" ]]; then
  systemctl --user stop omarchy-sesh.service omarchy-sesh-autosave.service >/dev/null 2>&1 || true
  systemctl --user disable omarchy-sesh.service omarchy-sesh-autosave.service >/dev/null 2>&1 || true
  rm -f "$UNIT" "$AUTOSAVE_UNIT"
  systemctl --user daemon-reload
  removed=1
fi

if [[ -f "$BIN" ]]; then
  rm -f "$BIN"
  removed=1
fi

if [[ -f "$AUTOSTART" ]] && grep -qF "$AUTOSTART_LINE" "$AUTOSTART"; then
  # Remove the marker comment + the restore line added by the installer.
  sed -i "/omarchy-sesh: restore saved windows after login/d; /$AUTOSTART_LINE/d" "$AUTOSTART"
  removed=1
fi

if [[ -f "$DB" ]]; then
  rm -f "$DB"
  removed=1
fi

if [[ -d "$CONFIG_DIR" ]]; then
  rm -rf "$CONFIG_DIR"
  removed=1
fi

if [[ -d "$LOG_DIR" ]]; then
  rm -f "$LOG_DIR/omarchy-sesh.log"
  rmdir "$LOG_DIR" >/dev/null 2>&1 || true
  removed=1
fi

if (( removed )); then
  echo "omarchy-sesh: removed all installed traces."
else
  echo "omarchy-sesh: nothing to remove."
fi
