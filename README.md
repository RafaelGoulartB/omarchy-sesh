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
./install.sh
```

Installs (user-level, no sudo needed) and is idempotent:

- binary → `~/.local/bin/omarchy-sesh`
- both systemd user units → `~/.config/systemd/user/` (enable + start)
- restore hook appended to `~/.config/hypr/autostart.lua` (skipped if already present)

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

## Development roadmap

See `tasks/next-steps.md` for the full roadmap: reboot acceptance test,
workspace→monitor remap, `stableId` matching, toggle, config surface, and a
system-level `/usr/bin` install variant.
# omarchy-sesh
