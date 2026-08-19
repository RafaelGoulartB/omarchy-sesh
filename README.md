# omarchy-sesh

Restore window positions and running apps after reboot or shutdown on
Omarchy (Hyprland). Snapshots live in a SQLite DB at
`${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/session.db`.

Scope: floating windows restore pixel-exact. Complete, uniquely matched nested
dwindle layouts with one unambiguous recursive split are rebuilt from their
saved rectangles and verified. Simple two-window sizing and compatible-slot
correction remain the fallback. This is still best-effort because Hyprland does
not expose its split tree. App content (browser tabs, unsaved docs, tmux
sessions) stays application-owned. See `docs/session-restore-spec.md`.

## How it works

**Save** (`omarchy-sesh save`) queries clients, monitors, and workspaces through
`hyprctl -j`, filters out unmapped windows and excluded classes, and reads each
remaining window's real command line and cwd from `/proc/<pid>/{cmdline,cwd}`.
Position, size, workspace, monitor connector and description, and
floating/fullscreen/pinned state are captured alongside the launch command and
written as one `sessions` row plus window and workspace-layout rows in the
SQLite DB. Complete Hyprland group membership and member order are translated
from live addresses to snapshot-local metadata. Windows sharing a saved PID are
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
browser command. After matching, eligible nested dwindle workspaces are rebuilt
in one fast Lua evaluation: one seed remains on the target workspace while the
other leaves pass through a temporary staging workspace. Focus, insertion
direction, and split ratios recreate the saved geometry, which is then verified.
Each saved workspace returns to the same connected monitor;
renamed or rewired outputs are identified by monitor description. Workspaces
from disconnected displays fall back to the focused monitor, then the lowest
monitor ID. `--dry-run` prints the launch plan without executing it.

On Hyprland 0.56 or newer, complete and uniquely matched window groups are
re-formed in saved member order after placement. Partial or ambiguous groups,
unrelated existing groups, and groups containing fullscreen or pinned windows
are left unchanged. Hyprland does not expose the saved active tab or lock/deny
state; reconstructed groups select their first saved member. Hyprland 0.55
continues to restore the windows without grouping them.

**Autosave** (`omarchy-sesh autosave`) waits one interval before its first
capture, then saves periodically (default 60s, configurable). It remains gated
until restore succeeds, so a partial login cannot replace the reboot snapshot,
and refreshes the active Hyprland instance before every capture. Explicitly
selecting Active mode captures the current desktop first when no successful
restore marker exists. A synchronous logout, reboot, or shutdown save closes
the gate before capture so a periodic save cannot supersede it during teardown.

Nested replay requires a schema-v5 snapshot, `dwindle:use_active_for_splits =
true`, `dwindle:preserve_split = true`,
`dwindle:permanent_direction_override = false`, every saved tiled window to be
present and uniquely identified, no unrelated tiled occupants, unchanged
workspace dimensions, and geometry with one unambiguous recursive split. Apps
continue to launch in parallel. Replay runs independently per workspace and
restores the focused window or empty workspace afterward; because Hyprland
layout messages act on the focused workspace, a very brief workspace change may
still be visible during login.
Ineligible workspaces retain the existing two-window ratio and compatible-slot
fallback without moving unrelated windows. Hyprland's `stableId` is only a live
compositor window-object selector and resets with Hyprland, so ambiguous
same-class windows still depend on title and workspace metadata. See
`docs/session-restore-spec.md` for the full design rationale and limitations.
When a disconnected display falls back to a monitor with different dimensions,
saved floating coordinates may require manual adjustment.

## Requirements

- Hyprland >= 0.55 (uses the Lua dispatch API)
- python3 (stdlib only — sqlite3, json, shlex)

Nested tiled replay requires Hyprland >= 0.56 with the three dwindle options
listed above. Ordinary restore remains available when those conditions are not
met.

## Installing as an Omarchy Plugin

The plugin is published as an Omarchy bar widget with manifest id
`mrpbennett.sesh` (kind `bar-widget`). Use the Omarchy plugin manager instead of
running `install.sh` by hand.

### Install

```sh
omarchy plugin add https://github.com/mrpbennett/omarchy-sesh.git --enable
```

What happens:

1. Omarchy clones the repository into a staging directory.
2. It validates the folder against the Omarchy plugin manifest schema
   (`omarchy plugin validate`); invalid plugins are refused.
3. It reads the manifest id (`mrpbennett.sesh`) and moves the checkout to
   `~/.config/omarchy/plugins/mrpbennett.sesh/`.
4. It rescans shell plugins and, because the manifest declares a bar widget,
   asks which bar section to place it in (default `right`).
5. It enables the widget with `omarchy plugin enable mrpbennett.sesh --section <section>`.

Verify placement with:

```sh
omarchy plugin list
```

To update a git-managed plugin:

```sh
omarchy plugin update mrpbennett.sesh
```

To remove it, see [Uninstall](#uninstall).

### First Use

The bar widget is a button showing a session icon. Opening its panel on a fresh
install runs the plugin's installation check: it verifies the CLI binary, the
two systemd user units, and the version marker. If any are missing, it runs the
bundled `install.sh` automatically, which deploys (user-level, no sudo):

- `~/.local/bin/omarchy-sesh`
- `~/.config/systemd/user/omarchy-sesh.service` and
  `omarchy-sesh-autosave.service`
- marker-delimited pre-shutdown save actions in
  `~/.config/omarchy/extensions/omarchy-menu.jsonc`, preserving any
  user-customized logout, reboot, and shutdown actions

After installation the widget shows three actions:

- **Active** — enables periodic autosave (default interval 60s).
- **Manual** — disables autosave and saves the current session immediately.
- **Restore** — relaunches the latest snapshot's apps and window layout.

On a fresh install autosave defaults to manual mode. Selecting **Active**
captures the current desktop once and enables the periodic saver. A restore then
runs at the next graphical login via `omarchy-sesh.service`; the autosave
service is ordered after it and waits one interval before its first capture.
Saves before logout, reboot, or shutdown happen synchronously so the reboot
snapshot is never superseded by a periodic capture during teardown.

See [How it works](#how-it-works) for the full save/restore behavior and
`install.sh`/`Service.qml` for the installation and first-open wiring.

## CLI

```sh
omarchy-sesh save [--label manual|logout]   # snapshot current windows
omarchy-sesh restore [--dry-run]            # relaunch latest snapshot
omarchy-sesh autosave [--interval 60]       # periodic save (crash cover)
omarchy-sesh status                         # list saved sessions
omarchy-sesh mode [active|manual]           # query or change autosave mode
omarchy-sesh acceptance [--expect-power-save|--expect-restore-failure]
                                            # read-only live acceptance evidence
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

See `docs/future-improvements.md` for the full roadmap, including live reboot
acceptance, stable window identity, window groups, configuration, and upgrade
coverage.

## Live Reboot Acceptance

`omarchy-sesh acceptance` only reads systemd, Hyprland, and saved session state;
it never saves, restores, changes mode, or powers off. Follow
`docs/live-acceptance.md` for the controlled reboot, power-menu, and
failure-recovery procedures.
