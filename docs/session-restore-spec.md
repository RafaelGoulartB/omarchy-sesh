# Omarchy Session Restore — Design Spec

## 1. Goal and honest scope

**Goal:** after reboot or shutdown, relaunch the apps that were open and put each
window back where it was.

**What this can achieve (the ceiling):**

| Aspect | Outcome |
|---|---|
| App relaunch | Exact — reconstruct launch command from `/proc/<pid>/cmdline` + cwd |
| Floating window geometry | Exact — Hyprland dispatchers place/resize by pixel |
| Tiled window placement | Best-effort — windows relaunch onto the saved workspace; if Hyprland recreates the same geometry slots, matched occupants are swapped into their saved slots. The dwindle/master split tree is not exposed via IPC, so missing splits and ratios cannot be reconstructed |
| Workspace assignment | Yes — launch into the saved workspace and move the matched window there |
| Monitor remapping | Not yet — saved monitor metadata is retained for a future layout-aware remap |
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
│  omarchy-sesh (single Python script, ~/.local/bin)        │
│                                                            │
│  save:    hyprctl -j clients  +  /proc/<pid>  →  sqlite    │
│  restore: sqlite                →  hyprctl dispatchers     │
└────────────────────────────────────────────────────────────┘
         ▲                        │
   started by               fires on startup,
   systemd service          logout, power menu
```

No Omarchy or Hyprland source change. Storage is sqlite at
`${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/session.db` (a plain host file — not something
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
  - `hl.dsp.window.swap({ window = …, target = … })` — exchange two tiled
    windows while retaining their layout slots
  - `hl.dsp.window.move({ workspace = <N>, follow = false, window = … })` —
    silent workspace move
  - `hl.dsp.workspace.move({ workspace = <N>, monitor = "<NAME>" })` — move a
    workspace to a monitor (workspace must exist first)
- UWSM caveat (Omarchy uses `uwsm-app`): **never** trigger restore or save via
  the `exit` dispatcher or by killing Hyprland — use `uwsm stop` / loginctl so
  session teardown stays ordered.

## 3. Storage schema (sqlite)

DB path: `${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/session.db`.

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
    at_x          INTEGER, at_y INTEGER,    -- exact float position or tiled slot metadata
    size_w        INTEGER, size_h INTEGER,
    floating      INTEGER NOT NULL DEFAULT 0,
    fullscreen    INTEGER NOT NULL DEFAULT 0,  -- 0/1/2
    pinned        INTEGER NOT NULL DEFAULT 0,
    xwayland      INTEGER NOT NULL DEFAULT 0,
    pid           INTEGER,                  -- groups windows from one process
    saved_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sessions (
    id        INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    label     TEXT,                      -- 'manual' | 'logout' | 'periodic'
    capture_status TEXT NOT NULL,        -- complete | partial | failed
    capture_error TEXT
);

CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY, value TEXT
);
INSERT INTO schema_meta VALUES ('version','2');
```

Rules:
- Restore selects the newest `complete` capture. A healthy zero-window capture
  is authoritative; partial, failed, and ambiguous legacy captures are never
  restore sources.
- Only save windows with `mapped == true` and non-empty `cmdline`. Drop the
  Omarchy shell, bars, panels, trays, polkit agents, and whatever the
  autostart already launches (exclude list in config, see §6).
- A window address is never persisted as a lookup key. Saved PIDs group launch
  commands only; restore-time windows are matched one-to-one by class, title,
  initial metadata, and workspace.

## 4. Save path (`omarchy-sesh save`)

1. `hyprctl -j clients` → list of mapped windows.
2. For each, read `/proc/<pid>/cmdline` and `/proc/<pid>/cwd` (resolve cwd
   symlink). Skip windows whose cmdline is empty, is `hyprctl`, or is in the
   exclude list.
3. Group windows by saved PID. Determine numeric `workspace_id`; resolve `monitor_name` via
   `hyprctl -j monitors` (map `monitor` id → name). Retain `at` and `size` for
   tiled windows as slot identity metadata, not as pixel-placement commands.
4. `INSERT` into a new session row in one transaction.

Triggers (any one fires a save):

| Trigger | Mechanism |
|---|---|
| Clean logout / reboot / shutdown | Power-menu entries (see §6) run `omarchy-sesh save` before `uwsm stop` / `loginctl` |
| Systemd teardown diagnostic | `ExecStop=omarchy-sesh save --teardown`; never supersedes a healthy snapshot |
| Periodic (crash cover) | systemd daemon, every 60 s, writes a `periodic` snapshot |

## 5. Restore path (`omarchy-sesh restore`)

Runs once from the systemd user service. Startup IPC failures return nonzero;
the service retries after two seconds.

1. Acquire an advisory operation lock and load the newest complete snapshot.
   A complete empty snapshot restores nothing. Match existing windows
   one-to-one and compare class multiplicities for the already-restored guard.
2. Build saved PID groups in window order and dispatch every missing group
   immediately, without waiting for an earlier application to start. Then poll
   all outstanding rows together within one shared 20-second deadline, placing
   each window as soon as it is matched. If one launch does not recreate every
   saved window, retry that group independently after a short grace period, up
   to the number of windows initially missing from the group.
   - Chromium app-mode windows reconstruct their URL from strict class metadata
     validated against either the initial title or saved `--app` argument and
     launch each through `omarchy-launch-webapp`, because the base Chromium
     process does not reopen those windows after reboot. Chromium's `Default`
     and `Profile_N` class suffixes are treated as the same web-app identity.
   - Launch through `hl.dsp.exec_cmd` with the saved silent workspace and
     floating rules.
   - Discover windows by polling and matching class, initial class/title,
     title, and workspace one-to-one.
    - Apply state through the Hyprland Lua dispatcher API: move to the saved
      workspace, set floating state, resize before moving floating windows,
      then restore fullscreen and pinned state.
    - After discovery, compare saved and current tiled geometries per workspace.
      When all saved tiled windows uniquely match the complete current slot set,
      swap occupants by address into their saved slots. Skip correction for
      missing, extra, fullscreen, ambiguous, or incompatible windows rather
      than altering an uncertain layout.
3. Saved monitor metadata is not yet applied. Workspace-to-monitor remapping
   remains a documented future improvement.

Restore failures return nonzero so systemd can retry startup IPC failures. Log
details to `${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/log/omarchy-sesh.log`.

## 6. Omarchy integration points

All confirmed against the installed Omarchy defaults.

1. **Startup** — `omarchy-sesh.service` is the only restore trigger. Older
   installer-owned Hyprland autostart lines are removed during upgrade. An
   advisory lock still protects manual concurrent invocations.

2. **systemd user service** — mirror Omarchy's shipped unit pattern
   (`/usr/share/omarchy/default/systemd/user/omarchy-crash-watch.service`,
   enabled via `install/user/first-run/enable-user-units.sh`):
   ```ini
   [Unit]
   Description=Omarchy session restore
    PartOf=graphical-session.target

   [Service]
   Type=oneshot
    ExecStart=%h/.local/bin/omarchy-sesh restore
    ExecStop=-%h/.local/bin/omarchy-sesh save --label logout --teardown
    RemainAfterExit=yes
    Restart=on-failure
    RestartSec=2

   [Install]
   WantedBy=graphical-session.target
   ```
   `ExecStop` is diagnostic because graphical teardown may already have removed
   clients. It cannot replace the newest complete snapshot.

3. **Power-menu / logout wiring** — marker-delimited user menu overrides save
   synchronously, then invoke `omarchy-system-logout`, `omarchy-system-reboot`,
   or `omarchy-system-shutdown`. Direct power commands bypass these overrides
   and rely on the latest periodic snapshot.

4. **Hook mechanism** — Omarchy has no pre-logout hook. Do not add another
   startup hook because that recreates the duplicate-restore race.

5. **Config / exclude list** — `${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/sesh/config.json`:
   `exclude_classes` (defaults skip polkit/portal agents), `autosave_seconds`
   (default 60). Save reads exclusions and autosave reads its interval at startup.

## 6a. Prototype status (verified live on Hyprland 0.56.2)

`bin/omarchy-sesh` — python3, stdlib only (sqlite3, json, shlex). Subcommands:

- `save [--label X]` — snapshots mapped clients + `/proc/<pid>` cmdline/cwd
  into `${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/session.db` (WAL,
  status-aware snapshots).
- `restore [--dry-run]` — loads the latest complete session, matches existing
  windows one-to-one, launches each saved PID group with bounded retries for
  missing windows, and places every matched window. Lua arguments use
  collision-free long strings.
- `autosave [--interval N]` — periodic save loop (crash cover). It refreshes
  the current Hyprland instance from the systemd user manager before each
  capture so a startup restore retry cannot leave it on a stale compositor.
  Failed restore markers keep autosave gated until restore succeeds or the user
  explicitly establishes a new baseline through manual/Active mode.
- `status` — lists recent sessions.

Verified end-to-end: save → close app → `restore` relaunched Nautilus and
placed it at the exact saved `at [145,75] size [1000,700]` floating. Test
windows were cleaned up after each run.

`systemd/user/omarchy-sesh.service` is the single restore trigger and retries
temporary lock or IPC failures. Application launch/placement failures are not
automatically relaunched in a loop. Autosave waits one interval and remains
gated while startup restore is retryable. Saves retain the latest five complete
and five diagnostic snapshots.

Not yet implemented: workspace→monitor remap on restore (monitor layout can
change across reboots), tab-group re-formation, and `stableId` matching.

## 7. Open decisions

- **Language:** bash + `sqlite3` CLI (matches Omarchy script style) vs a
  single python3 script with stdlib `sqlite3`/`json` (cleaner JSON + SQL,
  still zero deps). Recommend python3 for parse robustness; bash wrapper for
  the omarchy-* command surface.
- **`stableId`:** Hyprland 0.56 clients expose `stableId`. Whether it is
  stable across compositor restarts (vs. per-run `address`) is unverified —
  worth testing as a more robust restore-time match key than class+title.
- **Manual vs automatic restore:** default restore on every login, with a
  "don't restore" toggle (`${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/toggles/…`, matching
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
