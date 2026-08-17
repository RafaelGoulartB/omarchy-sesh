# Omarchy Session Restore — Design Spec

## 1. Goal and honest scope

**Goal:** after reboot or shutdown, relaunch the apps that were open and put each
window back where it was.

**What this can achieve (the ceiling):**

| Aspect | Outcome |
|---|---|
| App relaunch | Exact — reconstruct launch command from `/proc/<pid>/cmdline` + cwd |
| Floating window geometry | Exact — Hyprland dispatchers place/resize by pixel |
| Tiled window placement | Best-effort — windows relaunch in saved order onto the saved workspace; the dwindle/master split tree is not exposed via IPC, so tiling is re-derived, not pixel-identical |
| Workspace / monitor assignment | Yes — launch into workspace via rule prefix; move workspace to saved monitor |
| Window flags (float/fullscreen/pinned) | Yes — `setfloating`, `fullscreenstate`, `pin` |
| App *content* (tabs, unsaved docs, shell sessions) | No — that is application-owned. Browsers/tmux/editors restore their own content |

**Why:** Wayland's `xdg-shell` removed client-set window positions — the
compositor owns placement. Hyprland has no native session restore (no
`hyprctl restore`; `misc:allow_session_lock_restore` is unrelated; `persistent`
workspace rules only keep *empty* workspaces alive). The 2026
`xx-session-management-v1` protocol is the only real fix and Hyprland has not
implemented it. So restore is an external tool that reads a saved snapshot and
drives `hyprctl`.

## 2. Architecture

```
┌────────────────────────────────────────────────────────────┐
│  omarchy-sesh (single bash + python3 script, /usr/bin)    │
│                                                            │
│  save:    hyprctl -j clients  +  /proc/<pid>  →  sqlite    │
│  restore: sqlite                →  hyprctl dispatchers     │
└────────────────────────────────────────────────────────────┘
         ▲                        │
   started by               fires on startup,
   systemd service          logout, power menu
   + exec-once hook
```

No Omarchy or Hyprland source change. Storage is sqlite at
`~/.local/state/omarchy/session.db` (a plain host file — not something
Hyprland reads; Hyprland has no read path, the daemon is the reader).

Verified against Hyprland 0.56.2 (Lua dispatch API, not the pre-0.55 hyprlang
dispatchers). Key facts confirmed live:

- `hyprctl -j clients` returns per window: `address`, `at [x,y]`, `size [w,h]`,
  `workspace {id,name}`, `monitor` (id), `class`, `title`, `initialClass`,
  `initialTitle`, `pid`, `floating`, `pinned`, `fullscreen`, `fullscreenClient`,
  `grouped`, `tags`, `stableId`, `xwayland`, `mapped`, `hidden`.
  `fullscreen` encoding: `0` none, `1` maximized, `2` fullscreen.
- `hyprctl -j workspaces` returns `id`, `name`, `monitor`, `tiledLayout`,
  `lastwindowtitle`, `ispersistent`.
- **Launch with rules** (0.56 Lua form — the old `[workspace N silent]` prefix
  is NOT honored by `exec_cmd`):
  `hyprctl dispatch 'hl.dsp.exec_cmd("<cmd>", { workspace = "<N> silent", float = true })'`
  Returns `ok`, no PID — the spawned window must be discovered by polling
  `hyprctl -j clients` for a new address.
- **Placement dispatchers** (0.56 Lua form, all accept `window =
  "address:0x…"`):
  - `hl.dsp.window.move({ x = <abs_x>, y = <abs_y>, window = … })` — absolute
    position; add `relative = true` for delta.
  - `hl.dsp.window.resize({ x = <w>, y = <h>, window = … })` — exact size.
    **Important:** resize is center-anchored, so resize *then* move for
    pixel-exact placement.
  - `hl.dsp.window.float({ action = "on", window = … })`
  - `hl.dsp.window.fullscreen_state({ internal = <0|1|2>, client = <0|1|2>, window = … })`
  - `hl.dsp.window.pin({ window = … })` (floating only)
  - `hl.dsp.window.move({ workspace = <N>, follow = false, window = … })` —
    silent workspace move
  - `hl.dsp.workspace.move({ workspace = <N>, monitor = "<NAME>" })` — move a
    workspace to a monitor (workspace must exist first)
- UWSM caveat (Omarchy uses `uwsm-app`): **never** trigger restore or save via
  the `exit` dispatcher or by killing Hyprland — use `uwsm stop` / loginctl so
  session teardown stays ordered.

## 3. Storage schema (sqlite)

DB path: `~/.local/state/omarchy/session.db`.

```sql
PRAGMA journal_mode = WAL;

-- One row per saved window. PRIMARY KEY is a stable identity, not the
-- per-run address (addresses change every launch).
CREATE TABLE windows (
    id            INTEGER PRIMARY KEY,      -- restored-launch order (tiling)
    session       INTEGER NOT NULL,         -- FK -> sessions.id
    class         TEXT NOT NULL,            -- client class
    title         TEXT,
    initial_class TEXT,
    initial_title TEXT,
    cmdline       TEXT NOT NULL,            -- argv join(' ') from /proc/pid/cmdline
    cwd           TEXT,                     -- /proc/pid/cwd
    workspace_id  INTEGER NOT NULL,         -- numeric workspace at save time
    workspace_name TEXT,
    monitor_name  TEXT,                     -- hyprctl monitor name (e.g. DP-2)
    at_x          INTEGER, at_y INTEGER,    -- NULL for tiled windows
    size_w        INTEGER, size_h INTEGER,
    floating      INTEGER NOT NULL DEFAULT 0,
    fullscreen    INTEGER NOT NULL DEFAULT 0,  -- 0/1/2
    pinned        INTEGER NOT NULL DEFAULT 0,
    xwayland      INTEGER NOT NULL DEFAULT 0,
    pid           INTEGER,                  -- for debugging only
    saved_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sessions (
    id        INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    label     TEXT                       -- 'manual' | 'logout' | 'periodic'
);

CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY, value TEXT
);
INSERT INTO schema_meta VALUES ('version','1');
```

Rules:
- Save is **replace-all**: a new save clears previous rows for that session
  type and inserts a fresh snapshot. Keep the most recent `logout`/`manual`
  snapshot as the restore source; `periodic` saves only back up the last one.
- Only save windows with `mapped == true` and non-empty `cmdline`. Drop the
  Omarchy shell, bars, panels, trays, polkit agents, and whatever the
  autostart already launches (exclude list in config, see §6).
- A window address is never persisted as a lookup key — it is matched at
  restore time by `pid` (of the process we spawned) or by
  `class`+`initial_title`+workspace.

## 4. Save path (`omarchy-sesh save`)

1. `hyprctl -j clients` → list of mapped windows.
2. For each, read `/proc/<pid>/cmdline` and `/proc/<pid>/cwd` (resolve cwd
   symlink). Skip windows whose cmdline is empty, is `hyprctl`, or is in the
   exclude list.
3. Determine numeric `workspace_id`; resolve `monitor_name` via
   `hyprctl -j monitors` (map `monitor` id → name).
4. `INSERT` into a new session row in one transaction.

Triggers (any one fires a save):

| Trigger | Mechanism |
|---|---|
| Clean logout / reboot / shutdown | Power-menu entries (see §6) run `omarchy-sesh save` before `uwsm stop` / `loginctl` |
| Systemd teardown cover | `ExecStop=omarchy-sesh save` on the session-bound unit (§5) — catches crashes and power-cut-free logouts |
| Periodic (crash cover) | systemd timer / daemon, every 60 s, replaces the `periodic` snapshot |

## 5. Restore path (`omarchy-sesh restore`)

Runs once, from the Hyprland startup hook, after the compositor socket is up
(`sleep 2` standard).

1. Load most recent non-empty snapshot. **Double-restore guard:** if the number
   of current mapped clients already equals or exceeds the snapshot count
   (e.g. Hyprland restarted mid-session via `hyprctl reload`-style restart with
   windows still alive), abort. Compare by class set, not raw count.
2. For each saved window, in `id` order (preserves tiling order):
   a. **Skip** if a client with the same `class` is already present (single-
      instance apps: browsers, Electron, Discord).
   b. Launch: `hyprctl dispatch exec -- '[workspace <ws> silent float?] <cmdline>'`
      — use `float` only when the saved window was floating.
   c. Discover the new window: poll `hyprctl -j clients` for a client whose
      `pid` equals the pid `hyprctl dispatch exec` reported (the shell may
      daemonize; match by spawned pgid fallback), within a timeout (~5 s).
   d. Apply state by `address:<new>`:
      - workspace: `movetoworkspacesilent <ws>,address:<addr>`
      - floating: `setfloating address:<addr>` then
        `movewindowpixel exact <x> <y>,address:<addr>` and
        `resizewindowpixel exact <w> <h>,address:<addr>` (skip if saved `at` is NULL)
      - fullscreen: `fullscreenstate <n> <n>,address:<addr>`
      - pinned: `pin address:<addr>`
3. Monitor remap: if a saved `monitor_name` exists in the current
   `hyprctl -j monitors`, `moveworkspacetomonitor <ws> <monitor>` after launch;
   otherwise leave the workspace on the active monitor.

Non-blocking: restore the whole set without waiting for each window to finish
launching; per-window placement retries a couple of times then gives up.
Log failures to `~/.local/state/omarchy/log/omarchy-sesh.log`.

## 6. Omarchy integration points

All confirmed against the installed Omarchy defaults.

1. **Hyprland startup hook** — `~/.config/hypr/autostart.lua` (user override)
   or shipped default `default/hypr/autostart.lua`:
   ```lua
   hl.exec_cmd("sleep 2 && omarchy-sesh restore")
   ```
   The shipped default uses `hl.on("hyprland.start", function() … end)` and
   `o.launch_on_start`/`o.exec_on_start` (`default/hypr/helpers.lua:97-108`);
   restore should sit alongside the existing `sleep 2 && omarchy-hook post-boot`
   line.

2. **systemd user service** — mirror Omarchy's shipped unit pattern
   (`/usr/share/omarchy/default/systemd/user/omarchy-crash-watch.service`,
   enabled via `install/user/first-run/enable-user-units.sh`):
   ```ini
   [Unit]
   Description=Omarchy session restore
   After=graphical-session.target
   PartOf=graphical-session.target
   ConditionEnvironment=WAYLAND_DISPLAY

   [Service]
   Type=oneshot
   ExecStart=/usr/bin/omarchy-sesh restore
   ExecStop=/usr/bin/omarchy-sesh save
   RemainAfterExit=yes

   [Install]
   WantedBy=graphical-session.target
   ```
   `ExecStop` on `graphical-session.target` teardown gives clean-exit saves
   without touching the power menu. Install source in
   `/usr/share/omarchy/default/systemd/user/` and add it to
   `enable-user-units.sh` so it matches the existing provisioning flow.

3. **Power-menu / logout wiring** — prepend a save to the Omarchy logout,
   reboot, and shutdown commands (`omarchy-system-logout`,
   `omarchy-system-reboot`, `omarchy-system-shutdown` or the power menu entry
   they bind). Save via `uwsm stop`/`loginctl terminate-user`, never `hyprctl
   dispatch exit`.

4. **Hook mechanism** — `omarchy-hook` runs `~/.config/omarchy/hooks/<name>`
   and `<name>.d/` (`/usr/share/omarchy/bin/omarchy-hook`). A
   `post-boot.d/` hook could run restore, but the explicit `exec-once`
   approach above is more deterministic; the hook is a viable fallback the
   user can enable without editing autostart.

5. **Config / exclude list** — `~/.config/omarchy/sesh/config.json`:
   `exclude_classes` (defaults skip polkit/portal agents), `autosave_seconds`
   (default 60). The prototype reads it at save/restore time.

## 6a. Prototype status (verified live on Hyprland 0.56.2)

`bin/omarchy-sesh` — python3, stdlib only (sqlite3, json, shlex). Subcommands:

- `save [--label X]` — snapshots mapped clients + `/proc/<pid>` cmdline/cwd
  into `~/.local/state/omarchy/session.db` (WAL, replace-session-per-save).
- `restore [--dry-run]` — loads latest session, skips classes already present
  (single-instance guard) and aborts entirely if the session looks already
  restored (≥80% class overlap + count match). Launches each remaining window
  via `exec_cmd` with `workspace`/`float` rules, polls for the new address,
  then resize→move→fullscreen→pin.
- `autosave [--interval N]` — periodic save loop (crash cover).
- `status` — lists recent sessions.

Verified end-to-end: save → close app → `restore` relaunched Nautilus and
placed it at the exact saved `at [145,75] size [1000,700]` floating. Test
windows were cleaned up after each run.

`systemd/user/omarchy-sesh.service` (Type=oneshot, `RemainAfterExit=yes`,
`ExecStart=restore` / `ExecStop=save --label logout`, WantedBy
graphical-session.target) and `default/hypr/sesh-restore.lua` (the
`sleep 2 && omarchy-sesh restore` autostart snippet) ship alongside.
`systemd/user/omarchy-sesh-autosave.service` runs the periodic saver for the
whole graphical session (mirrors `omarchy-crash-watch`'s Type=simple +
Restart=always pattern) so window moves survive a power-off without logout.
Saves prune old snapshots (latest 5 kept) to keep the DB bounded.

Not yet implemented: workspace→monitor remap on restore (monitor layout can
change across reboots), tab-group re-formation, `stableId` matching, and
power-menu save wiring (relies on ExecStop for now).

## 7. Open decisions

- **Language:** bash + `sqlite3` CLI (matches Omarchy script style) vs a
  single python3 script with stdlib `sqlite3`/`json` (cleaner JSON + SQL,
  still zero deps). Recommend python3 for parse robustness; bash wrapper for
  the omarchy-* command surface.
- **`stableId`:** Hyprland 0.56 clients expose `stableId`. Whether it is
  stable across compositor restarts (vs. per-run `address`) is unverified —
  worth testing as a more robust restore-time match key than class+title.
- **Manual vs automatic restore:** default restore on every login, with a
  "don't restore" toggle (`~/.local/state/omarchy/toggles/…`, matching
  omarchy-crash-watch's toggle pattern).
- **Groups (tab groups):** `grouped` is saved but re-forming groups is not
  spec'd; first version restores members without grouping.

## 8. Verification plan

1. `omarchy-sesh save` → `sqlite3 session.db 'select * from windows'` shows
   correct classes/geometry/cmdline.
2. Launch a test set: a floating window (e.g. a scratchpad terminal), a tiled
   terminal in a tmux session, a browser, a fullscreen/pinned window; reboot.
3. After login, confirm: apps relaunch, floating windows land at saved
   `at`/`size`, tiled windows land on saved workspaces in order, browser
   restores its own tabs.
4. Crash test: `kill -9` a window, `sleep 60`, reboot — periodic snapshot
   still restores.
5. No-window test: fresh session with no snapshot → restore no-ops.