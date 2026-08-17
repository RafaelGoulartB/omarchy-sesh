#!/bin/bash

set -euo pipefail

# Install omarchy-sesh (user-level, no sudo): binary, systemd user units, and
# pre-shutdown Omarchy menu actions. Idempotent: safe to rerun.
# Uninstall with ./uninstall.sh.

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_SRC="${BIN_SRC:-$ROOT/bin/omarchy-sesh}"
UNIT_DIR_SRC="$ROOT/systemd/user"
PLUGIN_VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/manifest.json")"

BIN="$HOME/.local/bin/omarchy-sesh"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
UNIT_DIR="$CONFIG_HOME/systemd/user"
INSTALL_MARKER="$STATE_HOME/omarchy/sesh-installed"
AUTOSTART="$HOME/.config/hypr/autostart.lua"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"

MARKER_COMMENT="# omarchy-sesh: restore saved windows after login (guard skips if already restored)"
LUA_MARKER_COMMENT="-- omarchy-sesh: restore saved windows after login (guard skips if already restored)"
RESTORE_LINE='hl.exec_cmd("sleep 2 && omarchy-sesh restore")'
SYSTEMCTL="${SYSTEMCTL:-systemctl}"

autosave_unit_existed=0
autosave_was_enabled=0
[[ -f "$UNIT_DIR/omarchy-sesh-autosave.service" ]] && autosave_unit_existed=1
if "$SYSTEMCTL" --user is-enabled omarchy-sesh-autosave.service >/dev/null 2>&1; then
  autosave_was_enabled=1
fi

install -d "$(dirname "$BIN")"
install -m 755 "$BIN_SRC" "$BIN"
echo "installed $BIN"

install -d "$UNIT_DIR"
cp "$UNIT_DIR_SRC/omarchy-sesh.service" "$UNIT_DIR_SRC/omarchy-sesh-autosave.service" "$UNIT_DIR/"
echo "installed units to $UNIT_DIR"

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
  echo "removed legacy duplicate restore hook from $AUTOSTART"
fi

if [[ ! -f "$MENU" ]]; then
  install -d "$(dirname "$MENU")"
  printf '{}\n' >"$MENU"
fi

python3 - "$MENU" <<'PY'
import sys
from pathlib import Path
import os
import re
import tempfile


def strip_comments(value):
    output = []
    index = 0
    quote = None
    while index < len(value):
        char = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if quote:
            output.append(char)
            if char == "\\" and following:
                output.append(following)
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            while index < len(value) and value[index] != "\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and following == "*":
            output.extend("  ")
            index += 2
            while index < len(value):
                if value[index:index + 2] == "*/":
                    output.extend("  ")
                    index += 2
                    break
                output.append("\n" if value[index] == "\n" else " ")
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def atomic_write(path, value):
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temporary = handle.name
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)

path = Path(sys.argv[1])
text = path.read_text()
code = strip_comments(text)
begin = "// omarchy-sesh: begin power-menu overrides"
end = "// omarchy-sesh: end power-menu overrides"
if (begin in text) != (end in text):
    print(f"error: incomplete omarchy-sesh block in {path}", file=sys.stderr)
    raise SystemExit(1)
if begin in text:
    print("power-menu overrides already present (skipped)")
    raise SystemExit(0)

actions = {
    "system.logout": ('󰍃', "Logout", "omarchy-system-logout"),
    "system.reboot": ('󰜉', "Reboot", "omarchy-system-reboot"),
    "system.shutdown": ('󰐥', "Shutdown", "omarchy-system-shutdown"),
}
entries = []
for menu_id, (icon, label, command) in actions.items():
    if re.search(rf'"{re.escape(menu_id)}"\s*:', code):
        print(f"warning: preserving customized {menu_id} action")
        continue
    action = (
        "$HOME/.local/bin/omarchy-sesh save --label logout --wait || true; "
        f"exec {command}"
    )
    entries.append(
        f'  "{menu_id}": {{"icon":"{icon}","label":"{label}","action":"{action}"}},'
    )
if not entries:
    raise SystemExit(0)

root = re.search(r"(?m)^[ \t]*\{", code)
if root is None:
    print(f"error: {path} is not a JSONC object", file=sys.stderr)
    raise SystemExit(1)

items = re.search(r'"items"\s*:\s*\{', code)
target = items or root
indent = "    " if items else "  "
block = "\n" + indent + begin + "\n" + "\n".join(entries) + "\n" + indent + end + "\n"
atomic_write(path, text[: target.end()] + block + text[target.end() :])
print(f"added pre-shutdown saves to {path}")
PY

"$SYSTEMCTL" --user daemon-reload
"$SYSTEMCTL" --user enable omarchy-sesh.service >/dev/null
if (( ! autosave_unit_existed || autosave_was_enabled )); then
  "$SYSTEMCTL" --user enable omarchy-sesh-autosave.service >/dev/null
  if (( autosave_was_enabled )); then
    "$SYSTEMCTL" --user try-restart omarchy-sesh-autosave.service >/dev/null
  fi
  echo "enabled omarchy-sesh.service and omarchy-sesh-autosave.service"
else
  echo "enabled omarchy-sesh.service; preserved manual autosave mode"
fi

install -d "$(dirname "$INSTALL_MARKER")"
printf '%s\n' "$PLUGIN_VERSION" >"$INSTALL_MARKER"

echo "omarchy-sesh: installed. Restore runs on next login; saves run before power-menu actions and periodically."
