#!/bin/bash

set -euo pipefail

# Remove all traces of omarchy-sesh from the user's system.
# Reverses bin/omarchy-sesh install, the systemd user unit, the Hyprland
# legacy autostart hook, power-menu overrides, session DB, lock, and logs.
# Idempotent: safe to rerun.

BIN="$HOME/.local/bin/omarchy-sesh"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
UNIT="$CONFIG_HOME/systemd/user/omarchy-sesh.service"
UNIT_WANTS="$CONFIG_HOME/systemd/user/graphical-session.target.wants/omarchy-sesh.service"
AUTOSAVE_UNIT="$CONFIG_HOME/systemd/user/omarchy-sesh-autosave.service"
AUTOSAVE_WANTS="$CONFIG_HOME/systemd/user/graphical-session.target.wants/omarchy-sesh-autosave.service"
AUTOSTART="$HOME/.config/hypr/autostart.lua"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
DB="$STATE_HOME/omarchy/session.db"
LOCK="$STATE_HOME/omarchy/session.lock"
RESTORE_MARKER="$STATE_HOME/omarchy/restore-complete.json"
INSTALL_MARKER="$STATE_HOME/omarchy/sesh-installed"
LOG_DIR="$STATE_HOME/omarchy/log"

MARKER_COMMENT="# omarchy-sesh: restore saved windows after login (guard skips if already restored)"
LUA_MARKER_COMMENT="-- omarchy-sesh: restore saved windows after login (guard skips if already restored)"
RESTORE_LINE='hl.exec_cmd("sleep 2 && omarchy-sesh restore")'
SYSTEMCTL="${SYSTEMCTL:-systemctl}"

removed=0

"$SYSTEMCTL" --user stop omarchy-sesh-autosave.service omarchy-sesh.service >/dev/null 2>&1 || true
"$SYSTEMCTL" --user disable omarchy-sesh-autosave.service omarchy-sesh.service >/dev/null 2>&1 || true

for link in "$UNIT_WANTS" "$AUTOSAVE_WANTS"; do
  if [[ -L "$link" || -f "$link" ]]; then
    rm -f "$link"
    removed=1
  fi
done

if [[ -f "$UNIT" || -f "$AUTOSAVE_UNIT" ]]; then
  rm -f "$UNIT" "$AUTOSAVE_UNIT"
  "$SYSTEMCTL" --user daemon-reload
  removed=1
fi

if [[ -f "$MENU" ]] && grep -qF "// omarchy-sesh: begin power-menu overrides" "$MENU"; then
  python3 - "$MENU" <<'PY'
import sys
from pathlib import Path
import os
import tempfile

path = Path(sys.argv[1])
text = path.read_text()
begin = "// omarchy-sesh: begin power-menu overrides"
end = "// omarchy-sesh: end power-menu overrides"
begin_pos = text.find(begin)
end_pos = text.find(end, begin_pos)
if begin_pos < 0 or end_pos < 0:
    print(f"error: incomplete omarchy-sesh block in {path}", file=sys.stderr)
    raise SystemExit(1)
start = text.rfind("\n", 0, begin_pos) + 1
finish = text.find("\n", end_pos)
updated = text[:start] + text[finish + 1 if finish >= 0 else len(text):]
with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
    handle.write(updated)
    temporary = handle.name
os.chmod(temporary, path.stat().st_mode)
os.replace(temporary, path)
PY
  removed=1
fi

if [[ -f "$BIN" ]]; then
  rm -f "$BIN"
  removed=1
fi

if [[ -f "$AUTOSTART" ]] && grep -qF -- "$RESTORE_LINE" "$AUTOSTART" \
  && { grep -qF -- "$MARKER_COMMENT" "$AUTOSTART" || grep -qF -- "$LUA_MARKER_COMMENT" "$AUTOSTART"; }; then
  python3 - "$AUTOSTART" "$MARKER_COMMENT" "$LUA_MARKER_COMMENT" "$RESTORE_LINE" <<'PY'
import sys
from pathlib import Path
import os
import tempfile

path = Path(sys.argv[1])
markers = set(sys.argv[2:4])
restore_line = sys.argv[4]
lines = path.read_text().splitlines(keepends=True)
for index in range(len(lines) - 1):
    if (
        lines[index].rstrip("\r\n") in markers
        and lines[index + 1].rstrip("\r\n") == restore_line
    ):
        del lines[index : index + 2]
        break
with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
    handle.writelines(lines)
    temporary = handle.name
os.chmod(temporary, path.stat().st_mode)
os.replace(temporary, path)
PY
  removed=1
fi

if [[ -f "$DB" || -f "$DB-wal" || -f "$DB-shm" || -f "$LOCK" || -f "$RESTORE_MARKER" || -f "$INSTALL_MARKER" ]]; then
  rm -f "$DB" "$DB-wal" "$DB-shm" "$LOCK" "$RESTORE_MARKER" "$INSTALL_MARKER"
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
