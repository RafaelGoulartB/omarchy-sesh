# Future Improvements

## Acceptance Testing

- [ ] **Reboot restore**: reboot with tiled, floating, browser, terminal, and
      multi-window applications open. Confirm each window returns to its saved
      workspace and floating geometry.
- [ ] **Service health**: after reboot, verify `omarchy-sesh.service` completes,
      autosave is active when enabled, and neither service enters a restart
      loop.
- [ ] **Omarchy plugin**: load the widget in the live shell, inspect both shield
      states, and exercise Active, Manual, and Restore.
- [ ] **Power menu**: confirm Omarchy logout, reboot, and shutdown actions save
      before Hyprland destroys its clients.
- [ ] **Failure recovery**: force one application launch to fail and confirm
      autosave does not replace the last complete snapshot.

## Restore Quality

- [x] **Monitor remapping implementation**: save connector and display identity,
      resolve renamed or rewired outputs by description, and move workspaces to
      a deterministic fallback when their saved monitor is disconnected.
- [ ] **Monitor remapping acceptance**: verify saved workspaces on disconnected,
      renamed, rewired, and reordered monitors, including floating geometry on
      a differently sized fallback display.
- [ ] **Stable identity**: investigate Hyprland `stableId` support and prefer it
      over title-based matching where it remains valid across compositor
      sessions.
- [ ] **Window groups**: restore Hyprland group membership after every member
      has been matched and placed.
- [ ] **Exact tiled layout serialization**: pursue a Hyprland API or native
      plugin that exports and restores dwindle/master split trees and ratios.
      This is the robust replacement for inferred pixel resizing, but a local
      plugin would require C++ code tied to Hyprland's unstable internal ABI and
      rebuilding for matching compositor versions.
- [ ] **Slow applications**: collect real startup timings and make the restore
      timeout configurable only if the current 20-second bound is insufficient.
- [ ] **Restore performance**: benchmark dispatch and window-discovery latency
      with larger sessions. Python is currently appropriate because restore is
      I/O-bound, applications launch concurrently, and its standard library
      provides robust JSON, SQLite, process, and `/proc` handling without extra
      dependencies. If the current 200 ms polling becomes a bottleneck,
      investigate Hyprland event notifications or persistent IPC before
      considering a language rewrite.
- [x] **Chromium app-mode launcher**: strictly recognized web-app windows launch
      individually through `omarchy-launch-webapp` when Chromium cannot recreate
      them through a bounded generic relaunch.
- [ ] **Additional launchers**: add more application-specific handling only for
      apps proven not to recreate their saved windows through bounded generic
      relaunches.

## Omarchy Integration

- [ ] **Plugin removal UX**: monitor Omarchy plugin lifecycle support. Replace
      the documented uninstall-before-remove sequence if official uninstall
      hooks become available.
- [ ] **Menu customization coverage**: test user menu files that customize
      power action labels, icons, conditions, or commands without overwriting
      user-owned behavior.
- [ ] **Configuration surface**: expose validated settings for excludes,
      autosave interval, restore timeout, snapshot retention, and monitor
      fallback. Preserve existing defaults when extending configuration.
- [ ] **Upgrade coverage**: test plugin upgrades from each released schema and
      manifest version while preserving Manual mode and existing snapshots.

## Release Readiness

- [x] Run the complete Python, Bash, systemd, and plugin validation suite.
- [ ] Perform one clean install, update, and uninstall in an isolated home.
- [ ] Document confirmed limitations and recovery commands in `README.md`.
- [ ] Remove generated artifacts and review the final release diff.

## Definition Of Done

An item is complete only when its behavior is covered by an automated test
where practical, verified on a live Omarchy/Hyprland session when integration
is involved, and documented without replacing existing Omarchy defaults.
