# omarchy-sesh — Next Steps

Work-in-progress roadmap. Check items off as they land. See
`docs/session-restore-spec.md` for the design and verified API surface.

## Current status (v0, wired live)

- [x] Prototype `bin/omarchy-sesh` — save / restore / autosave / status (python3, stdlib only)
- [x] Verified Hyprland 0.56.2 Lua dispatch API live; floating placement pixel-exact
- [x] `omarchy-sesh.service` (oneshot: restore on graphical-session.target start, save on stop)
- [x] `omarchy-sesh-autosave.service` (periodic saver for whole session, crash cover)
- [x] Autostart hook in `~/.config/hypr/autostart.lua` (belt-and-suspenders restore trigger)
- [x] Snapshot pruning (latest 5 kept), exclude list, WAL sqlite DB at `~/.local/state/omarchy/session.db`
- [x] `install.sh` / `uninstall.sh` — idempotent user-level install + full trace removal
- [x] Live-tested: save → close app → restore relaunches + places floating windows exact

## Short term (correctness — do before relying on it daily)

- [ ] **Reboot acceptance test** — save, reboot, confirm on login:
      apps relaunch, floating windows land at saved `at`/`size`, tiled windows
      land on saved workspaces in order, browser restores its own tabs. Then
      check `journalctl --user -u omarchy-sesh.service`.
- [ ] **Workspace→monitor remap** — restore currently assumes saved monitor
      names still exist. Save `monitor_name` (already stored); on restore map
      to the *first available* monitor if the saved one is gone, and remap
      workspace→monitor when the monitor layout changed across a reboot.
- [ ] **Single-instance guard review** — the ≥80% class-overlap guard aborts
      restore when windows are present. Confirm it never fires spuriously on a
      normal login (partial app overlap) and never false-positives a genuinely
      empty session.
- [ ] **Autosave label churn** — periodic saves insert a new row every 60s;
      pruning keeps 5, but `restore` always picks the newest (a `periodic`
      snapshot). Decide whether `logout`/`manual` should win over `periodic` at
      restore time (recency vs. intentionality).
- [ ] **Power-off safety** — verify the periodic daemon really captures window
      moves (`kill -9` a test window, wait 60s, reboot, confirm restore).

## Medium term (robustness)

- [ ] **`stableId` as restore match key** — Hyprland 0.56 exposes `stableId` per
      client; test whether it survives compositor restarts. If yes, use it to
      match restore-time windows (more robust than class+title), and to skip
      relaunch of apps that self-reopen (e.g. chromium `--restart`).
- [ ] **`--restart` handling** — chromium/electron apps carry a `--restart`
      flag from Omarchy's launch wrapper; decide whether to strip it on
      relaunch to avoid double-launch semantics.
- [ ] **Class dedupe / launch dedupe** — if two saved windows share a class and
      cmdline, relaunching both as separate instances needs care (gtk single
      instance flags, tray apps). Test with two terminals of same class.
- [ ] **Tab-group re-formation** — `grouped` is saved; first version restores
      members without grouping. Add group re-formation once grouping via IPC is
      confirmed possible (or document as out of scope).
- [ ] **Fullscreen/pinned restore order** — resize→move→fullscreen→pin sequence
      is verified; test restoring a window that is BOTH fullscreen and on a
      non-focused workspace (does pin+move order break focus/visibility?).
- [ ] **Toggle to skip restore** — `~/.local/state/omarchy/toggles/sesh-restore-off`
      file (mirroring `omarchy-crash-watch`'s toggle pattern) checked by both
      the service and the autostart hook, so users can turn restore off without
      uninstalling.
- [ ] **Config surface** — `~/.config/omarchy/sesh/config.json`:
      `exclude_classes`, `autosave_seconds`, `restore_workspaces`, `max_snapshots`,
      `default_monitor`. Wire autosave interval into the running daemon via the
      unit or a re-read loop (currently fixed at startup).
- [ ] **Log hygiene** — log to a bounded log (rotate/truncate); currently
      `~/.local/state/omarchy/log/omarchy-sesh.log` grows unbounded.

## Long term (polish / distribution)

- [ ] **Install to `/usr/bin` variant** — `install.sh --system` for a distro /
      sudo path (Omarchy's scripts live in `/usr/share/omarchy/bin`); user-level
      default stays the no-sudo path. Keep uninstall symmetric.
- [ ] **Omarchy provisioning integration** — add `enable-user-units.sh` entries
      + `default/hypr` snippet so a future bundled install matches Omarchy's
      first-run flow. Requires a maintainer decision on shipping inside Omarchy
      vs. standalone repo.
- [ ] **Power-menu save** — prepend save to Omarchy's logout/reboot/shutdown
      commands (`omarchy-system-*` / power menu) so the ExecStop path isn't the
      only clean-exit save.
- [ ] **Multi-session restore picker** — `restore --pick` lists saved sessions
      (already stored with labels) and restores a chosen one.
- [ ] **Tests** — a `tests/` dir with a fake `hyprctl` fixture (JSON from real
      captures) so save/restore SQL + matching logic is testable without a live
      compositor; CI via GitHub Actions.
- [ ] **Bump `/usr/bin` binaries in DB** — saved cmdlines may reference the
      install path; if system install lands, consider rewriting `/home/*/.local/bin`
      to `/usr/bin` at restore time.

## Review notes

- Floating geometry restores pixel-exact; tiled layout is best-effort (split
  tree not exposed via IPC) — documented limitation, not a bug.
- App content (tabs, unsaved docs, tmux panes) stays application-owned; the
  tool restores launches + placement only.
- Autostart hook + service ExecStart both fire restore; the overlap guard makes
  the redundant path a no-op. If it ever restores twice, fix the guard, not the
  wiring.