# omarchy-sesh

Restore window positions and running apps after reboot or shutdown on
Omarchy (Hyprland). Snapshot lives in a sqlite DB at
`${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/session.db`.

Scope: floating windows restore pixel-exact, tiled windows relaunch onto the
saved workspaces in order (best-effort — Hyprland does not expose the split
tree), and app content (browser tabs, unsaved docs, tmux sessions) stays
application-owned. See `docs/session-restore-spec.md`.

## How it works

**Save** (`omarchy-sesh save`) queries `hyprctl -j clients` and `hyprctl -j monitors`,
filters out unmapped windows and excluded classes, and for each remaining
window reads its real command line and cwd from `/proc/<pid>/{cmdline,cwd}`.
Position, size, workspace, monitor, and floating/fullscreen/pinned state are
captured alongside the launch command and written as one `sessions` row plus
one `windows` row per window in the sqlite DB. Windows sharing a saved PID are
kept as one launch group. The latest five complete and five diagnostic
snapshots are retained.

**Restore** (`omarchy-sesh restore`) loads the most recent complete session;
a healthy empty session intentionally restores nothing. An advisory lock
serializes restore and save operations. Existing windows are consumed
one-to-one using class, title, and workspace, and class multiplicities prevent
false already-restored matches. All missing saved PID groups launch immediately
via Hyprland's Lua dispatch API, then windows are matched and placed as they
appear during one shared bounded polling window. If a process does not recreate
all of its saved windows, its command is retried independently. Chromium app-mode
windows are restored individually through
Omarchy's web-app launcher because Chromium does not reopen them from the base
browser command. `--dry-run` prints the launch plan without executing it.

**Autosave** (`omarchy-sesh autosave`) waits one interval before its first
capture, then saves periodically (default 60s, configurable). It remains gated
until restore succeeds, so a partial login cannot replace the reboot snapshot,
and refreshes the active Hyprland instance before every capture. Explicitly
selecting Active mode captures the current desktop first when no successful
restore marker exists.

Because tiled windows are relaunched independently rather than replayed into
Hyprland's split tree, ordering onto the right workspace is best-effort —
see `docs/session-restore-spec.md` for the full design rationale and
known limitations.

## Requirements

- Hyprland >= 0.55 (uses the Lua dispatch API)
- python3 (stdlib only — sqlite3, json, shlex)

## Install

```sh
./install.sh
```

Installs (user-level, no sudo needed) and is idempotent:

- binary → `~/.local/bin/omarchy-sesh`
- both systemd user units → `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/`
- power-menu overrides → `${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/extensions/omarchy-menu.jsonc`

`omarchy-sesh.service` is the single restore trigger. The autosave service is
ordered after it and waits one interval before its first capture. Logout,
reboot, and shutdown menu actions save synchronously before Omarchy closes any
windows; `ExecStop` remains a diagnostic fallback and cannot replace a healthy
snapshot. Old snapshots are pruned automatically.
Newly enabled services start with the next graphical login; reinstalling does
not relaunch applications. Updates restart an already-running autosave process
so it uses the newly installed binary.

### Omarchy plugin

Install this repository with `omarchy plugin add <git-url> --enable`. The bar
widget installs the user-level binary and services through `install.sh` on its
first open. Its three actions enable autosave, switch to manual mode and save
immediately, or restore the latest snapshot. Reinstalling preserves manual
mode when autosave was disabled. Plugin updates are detected from the manifest
version and redeploy the binary and units on the next panel open. A running
autosave process is restarted to load the new binary; manual mode stays stopped.

## Usage

```sh
omarchy-sesh save [--label manual|logout]   # snapshot current windows
omarchy-sesh restore [--dry-run]            # relaunch latest snapshot
omarchy-sesh autosave [--interval 60]       # periodic save (crash cover)
omarchy-sesh status                         # list saved sessions
omarchy-sesh mode [active|manual]           # query or change autosave mode
```

Config (optional): `${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/sesh/config.json`

```json
{
  "exclude_classes": ["polkit-gnome-authentication-agent-1"],
  "autosave_seconds": 60
}
```

## Uninstall

Omarchy does not run lifecycle hooks when removing plugins. For a plugin
installation, uninstall the session services before removing the checkout:

```sh
${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/mrpbennett.sesh/uninstall.sh
omarchy plugin remove mrpbennett.sesh
```

For a direct repository installation, run:

```sh
./uninstall.sh
```

Removes every trace: the binary, both systemd user units and their enablement,
legacy Hyprland hook, marker-delimited power-menu overrides, session DB,
lock, and logs. User-authored configuration is preserved. Idempotent and safe
to rerun.

## Development roadmap

See `docs/future-improvements.md` for the full roadmap: reboot acceptance test,
workspace→monitor remap, `stableId` matching, toggle, config surface, and a
system-level `/usr/bin` install variant.
