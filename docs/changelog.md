# Changelog

All notable changes to `omarchy-sesh` are documented here. The project has one
tagged prerelease, `alpha`; changes after that tag are listed as unreleased.

## Unreleased

### Added

- Named session snapshots: `save --name NAME`, `restore --name NAME`, `list`,
  and `delete --name NAME`. Names are conflict-safe, retained independently of
  automatic snapshots, and schema version 6 migrates existing databases.
- Monitor-aware restoration records connector names and physical display
  descriptions, resolves renamed or rewired outputs, and uses a deterministic
  fallback for disconnected displays.
- Workspace-to-monitor remapping runs before window state and tiled layout
  restoration.
- Best-effort tiled layout correction restores simple two-window split ratios
  and swaps uniquely identified windows into compatible saved slots.
- Database schema version 3 migrates existing snapshots to include monitor
  descriptions.
- Database schema version 4 migrates existing snapshots to store nullable,
  snapshot-local window group membership and order.
- Database schema version 5 stores complete workspace layout type and bounds for
  guarded nested tiled replay; legacy snapshots continue using the fallback.
- Complete, uniquely matched and unambiguous nested dwindle layouts are rebuilt
  through staged public Lua dispatches and verified against saved rectangles.
- Hyprland 0.56+ restores complete, uniquely matched window groups in saved
  order after placement while preserving unrelated current groups.
- Regression coverage now includes monitor identity conflicts and fallbacks,
  tiled split sizing and slot correction, Chromium profiles, global matching,
  restore markers, XDG paths, and installer recovery.
- Validated configuration now controls restore timeout, per-status snapshot
  retention, and disconnected-monitor fallback alongside the existing exclude
  and autosave settings.

### Changed

- Malformed or unknown configuration fails restore closed before any side
  effects, and `omarchy-sesh.service` does not restart-loop on it. Save and
  autosave instead log the error and continue with the defaults, so a
  configuration typo cannot skip a logout snapshot or stop periodic saves.
- `omarchy-sesh mode` warns when autosave is enabled but not running.
- Window discovery uses ranked one-to-one assignment instead of greedy class
  matching, reducing incorrect matches between similar windows.
- Hyprland 0.55 remains supported and restores grouped windows independently;
  direct group reconstruction is capability-gated to 0.56 or newer.
- Restore dispatches all initially missing process groups before polling and
  places fast windows without waiting for slower applications.
- Restore suppresses Hyprland animations through the Lua configuration API
  while it places windows and puts the previous setting back afterwards, so a
  restored desktop no longer visibly shuffles itself into position after its
  windows have mapped.
- Each window's placement is applied in one Hyprland Lua evaluation instead of
  one `hyprctl` process per property, cutting a floating window's placement
  cost from about 33 ms to about 8 ms and landing every property inside a
  single compositor frame.
- Placement skips workspace, float, fullscreen, and pin dispatches whose live
  window already matches the snapshot.
- Chromium app-mode identity validation accepts supported profile suffix
  changes while rejecting unrelated or malformed classes and URLs.
- Ordinary Chromium relaunches strip shared app-mode arguments to avoid
  duplicate web-app windows.
- The panel reports unknown mode and incomplete installations more accurately
  and only initiates installation through an explicit user action.
- Installer updates preserve Manual mode, restart autosave only when it was
  already active, and retain user-owned power actions.

### Fixed

- Nautilus restore strips its internal `--gapplication-service` flag so the
  relaunched process opens a file-manager window instead of service mode only.
- Incomplete or failed restores now keep autosave gated so they cannot replace
  the latest complete snapshot.
- Restore marker writes are atomic, marker failures are retryable, and dry runs
  no longer alter restore state.
- Existing restore markers no longer bypass live compositor IPC checks.
- Autosave refreshes the Hyprland instance before every capture and clears
  stale compositor environment values.
- Synchronous power-action saves close the autosave gate under the operation
  lock so a periodic capture cannot supersede them during graphical teardown.
- Empty XDG environment variables no longer create relative state paths, and
  state accidentally written to those paths is migrated.
- Uninstall removes legacy and dangling artifacts and stops safely when service
  state cannot be verified.
- Tiled restore skips incomplete, ambiguous, differently oriented, differently
  bounded, or unsupported layouts instead of modifying an uncertain split tree,
  and recovers staged windows if nested replay verification fails.

## 0.1.0 (alpha) - 2026-08-17

### Added

- Dependency-free Python CLI with `save`, `restore`, `autosave`, `status`, and
  `mode` commands.
- SQLite session storage with in-place migrations, WAL mode, status-aware
  snapshots, and retention of five complete and five diagnostic captures.
- Capture of mapped Hyprland windows, launch commands, working directories,
  workspaces, geometry, and floating, fullscreen, pinned, and XWayland state.
- One-to-one existing-window matching and saved-PID launch grouping, including
  bounded retries for multi-window processes.
- Concurrent application launch with one shared restore deadline and
  collision-safe Lua dispatch arguments.
- Pixel-exact floating resize and placement, saved workspace assignment, and
  fullscreen and pinned state restoration.
- Strict Chromium web-app relaunch through `omarchy-launch-webapp`.
- Complete empty snapshots as authoritative restore sources while partial,
  failed, teardown, and ambiguous legacy captures remain diagnostic.
- Autosave crash cover that waits one interval and remains gated until startup
  restore succeeds for the current compositor instance.
- Active and Manual mode control, including baseline capture before enabling
  autosave when no successful restore marker exists.
- Advisory locking for save and restore operations and distinct retryable versus
  application-failure exit semantics.
- A single systemd startup restore service and an autosave service ordered after
  it.
- Omarchy bar widget with Active, Manual save, and Restore actions, status icon,
  keyboard and mouse controls, and asynchronous CLI orchestration.
- Idempotent user-level installation, upgrade repair, and symmetric uninstall.
- Marker-delimited power-menu actions that save synchronously before logout,
  reboot, or shutdown without replacing existing user actions.
- Optional `exclude_classes` and `autosave_seconds` configuration through XDG
  paths.
- Dry-run restore planning and state-file logging.

### Fixed

- Startup restore no longer competes with a duplicate Hyprland autostart hook.
- Teardown and failed captures cannot supersede the newest healthy snapshot.
- Reinstalling the plugin preserves Manual mode instead of silently enabling
  autosave.
