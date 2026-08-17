# omarchy-sesh

Restore window positions and running apps after reboot or shutdown on
Omarchy (Hyprland). Snapshot lives in a sqlite DB at
`~/.local/state/omarchy/session.db`.

Scope: floating windows restore pixel-exact, tiled windows relaunch onto the
saved workspaces in order (best-effort — Hyprland does not expose the split
tree), and app content (browser tabs, unsaved docs, tmux sessions) stays
application-owned. See `docs/session-restore-spec.md`.

## Requirements

- Hyprland >= 0.55 (uses the Lua dispatch API)
- python3 (stdlib only — sqlite3, json, shlex)

## Install

```sh
./bin/omarchy-sesh --help        # sanity check
install -m 755 bin/omarchy-sesh ~/.local/bin/omarchy-sesh
cp systemd/user/omarchy-sesh.service systemd/user/omarchy-sesh-autosave.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now omarchy-sesh.service omarchy-sesh-autosave.service
```

Then add the restore hook to `~/.config/hypr/autostart.lua`:

```lua
hl.exec_cmd("sleep 2 && omarchy-sesh restore")
```

`omarchy-sesh.service` restores on `graphical-session.target` and saves on
teardown (`ExecStop`); the autostart hook is a second restore trigger — the
double-restore guard makes the redundant path a no-op.
`omarchy-sesh-autosave.service` runs the periodic saver for the whole session
so window moves are captured even if the machine is powered off without a
logout. Old snapshots are pruned automatically (latest 5 kept).

## Usage

```sh
omarchy-sesh save [--label manual|logout]   # snapshot current windows
omarchy-sesh restore [--dry-run]            # relaunch latest snapshot
omarchy-sesh autosave [--interval 60]       # periodic save (crash cover)
omarchy-sesh status                         # list saved sessions
```

Config (optional): `~/.config/omarchy/sesh/config.json`

```json
{
  "exclude_classes": ["polkit-gnome-authentication-agent-1"],
  "autosave_seconds": 60
}
```

## Uninstall

```sh
./uninstall.sh
```

Removes every trace: the `~/.local/bin/omarchy-sesh` binary, both systemd user
units and their enablement, the Hyprland autostart hook line, the session DB,
config, and logs. Idempotent and safe to rerun.
# omarchy-sesh
