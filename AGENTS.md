# Agent Guide

## What This Application Does

`omarchy-sesh` restores an Omarchy/Hyprland desktop after login. It snapshots
open application windows, relaunches missing applications, and puts their
windows back on the saved workspaces with the saved state. Floating geometry is
pixel-exact; tiled geometry is best-effort because Hyprland does not expose its
split tree. Application content such as tabs, unsaved files, and terminal
sessions remains the application's responsibility.

The project is also an Omarchy `bar-widget` plugin. Its panel provides exactly
three user actions: Active autosave, Manual save, and Restore.

Read `README.md` for user-facing behavior and
`docs/session-restore-spec.md` for the detailed design and verified Hyprland API.

## Architecture

- `bin/omarchy-sesh`: the application. This is one Python 3 script using only
  the standard library. It owns configuration, SQLite migrations, capture,
  matching, process launch, placement, autosave, status, and mode control.
- `Panel.qml`: Omarchy shell panel and the three-action UI.
- `Service.qml`: installation checks and asynchronous CLI process orchestration
  for the panel.
- `SupermanIcon.qml`: active/manual status icon.
- `manifest.json`: Omarchy plugin metadata and bar-widget entry point.
- `systemd/user/omarchy-sesh.service`: the only startup restore trigger.
- `systemd/user/omarchy-sesh-autosave.service`: periodic crash-cover snapshots,
  ordered after startup restore.
- `install.sh`: idempotent user-level install. It deploys the CLI and units,
  preserves autosave mode on upgrades, and adds marker-delimited pre-shutdown
  actions without replacing user menu customizations.
- `uninstall.sh`: idempotent removal of installed artifacts and runtime state;
  user-authored configuration is retained.
- `tests/test_omarchy_sesh.py`: stdlib `unittest` coverage for the Python logic
  and isolated installer behavior.

## Runtime Flow

`save` reads `hyprctl -j clients` and `hyprctl -j monitors`, then reads each
mapped client's command line and cwd from `/proc/<pid>`. It writes a session and
ordered window rows to SQLite. Saved PIDs group windows belonging to one
process; they are not identities that survive reboot.

`restore` selects the newest complete snapshot, matches already-open windows
one-to-one, launches all missing process groups through Hyprland's Lua dispatch
API, and polls all outstanding windows within one shared deadline. Each matched
window is placed immediately. Strictly recognized Chromium app-mode windows are
launched individually through `omarchy-launch-webapp`.

`autosave` waits before its first capture and remains gated until restore has
completed for the current `HYPRLAND_INSTANCE_SIGNATURE`. Omarchy power-menu
overrides synchronously save before logout, reboot, or shutdown; the restore
service's `ExecStop` capture is diagnostic fallback only.

## State And Configuration

- Database: `${XDG_STATE_HOME:-~/.local/state}/omarchy/session.db`
- Operation lock: `${XDG_STATE_HOME:-~/.local/state}/omarchy/session.lock`
- Restore marker: `${XDG_STATE_HOME:-~/.local/state}/omarchy/restore-complete.json`
- Log: `${XDG_STATE_HOME:-~/.local/state}/omarchy/log/omarchy-sesh.log`
- Optional config: `${XDG_CONFIG_HOME:-~/.config}/omarchy/sesh/config.json`
- Installed CLI: `~/.local/bin/omarchy-sesh`

The configuration currently supports `exclude_classes` and `autosave_seconds`.
Do not assume paths under `$HOME` when an XDG override is available.

## Correctness Invariants

- A healthy complete empty snapshot is authoritative. Partial, failed,
  teardown, and ambiguous legacy snapshots must never supersede a complete one.
- Save and restore operations must hold the advisory operation lock. Exit 75
  means a temporary condition that systemd may retry; exit 1 represents an
  application or placement failure and intentionally prevents a relaunch loop.
- Window matching is one-to-one. Never deduplicate only by class, and never
  claim an unrelated pre-existing client while discovering launched windows.
- Launch grouping follows saved PID, except strict Chromium web-app rows. Launch
  every initial missing group before polling so a slow app cannot serialize the
  entire restore.
- Encode Hyprland dispatcher values as Lua strings with `lua_quote`; shell
  quoting is not valid for Lua dispatch arguments.
- Restore floating windows by resizing before moving because Hyprland resize is
  center-anchored. Tiled layout must remain documented as best-effort.
- Keep one startup restore trigger. Adding a Hyprland autostart invocation
  creates duplicate-restore races.
- The restore-complete marker is scoped to the compositor instance. Autosave
  must not overwrite the reboot snapshot before startup restore succeeds.
- Installer changes must remain idempotent, preserve manual autosave mode and
  user-authored menu actions, and have a symmetric uninstall path.
- Keep the CLI dependency-free unless the project explicitly changes that
  requirement. Existing databases must migrate in place when the schema changes.

## Development Workflow

Run the unit and isolated installer tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Run static integration checks after relevant changes:

```sh
bash -n install.sh uninstall.sh
systemd-analyze verify systemd/user/omarchy-sesh.service systemd/user/omarchy-sesh-autosave.service
omarchy plugin validate .
git diff --check
```

The plugin validator and systemd verifier require their host tools. `qmllint`
is not currently available in the documented development environment.

Do not run `install.sh`, `uninstall.sh`, non-dry-run restore, mode changes, or
live save/restore acceptance tests merely as generic verification: they modify
the user's desktop or installed state. Installer tests use temporary homes and
fake `systemctl`. Use `bin/omarchy-sesh restore --dry-run` only when a live
Hyprland session and its real saved database are intentionally part of the test.

## Change Guidance

- Add focused regressions in `tests/test_omarchy_sesh.py` for matching,
  scheduling, migration, installer, or failure-semantics changes.
- Update `README.md` when user-visible behavior or commands change.
- Update `docs/session-restore-spec.md` when architecture, state, dispatch API,
  restore guarantees, or limitations change.
- Consult `tasks/next-steps.md` for known gaps. Current notable limitations are
  workspace-to-monitor remapping, unverified cross-restart `stableId` behavior,
  tab-group reconstruction, and ambiguous same-class windows without stable
  titles.
