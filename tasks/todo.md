# Session Restoration Research

- [x] Research Hyprland native and third-party session restoration.
- [x] Inspect local Omarchy startup integration points.
- [x] Report feasibility and recommended implementation boundary.
- [x] Verify placement dispatchers + `hyprctl clients` JSON schema live (Hyprland 0.56.2).
- [x] Design daemon + sqlite schema; spec in `docs/session-restore-spec.md`.

## Review

- Hyprland has no native persistent-window session feature (`hyprctl reload` != restore; `misc:allow_session_lock_restore` is unrelated; `persistent` workspace rules only keep empty workspaces alive; upstream declined session restore).
- External tools (hyprsession, hypr-session-restore, etc.) all do: snapshot `hyprctl -j clients` + `/proc/<pid>` → relaunch + best-effort placement. None use socket2 for saving (poll/timer instead). `xx-session-management-v1` merged into wayland-protocols 2026-03; Hyprland has not implemented it.
- A user-level systemd service plus a Hyprland startup hook is the stable integration boundary; do not require an Omarchy or Hyprland source change.
- Full application state remains application-owned; restore can only reliably reconstruct application launches and best-effort placement.
- A host sqlite DB is the storage backend for the restore daemon — Hyprland itself has no read path for it; the daemon drives `hyprctl` dispatchers (`exec [workspace N silent]`, `movewindowpixel/resizewindowpixel exact`, `movetoworkspacesilent`, `setfloating`, `fullscreenstate`, `pin`).
- Floating geometry restores pixel-exact; tiled layout restores best-effort (split tree not exposed via IPC).

## Next steps

- [x] Prototype `omarchy-sesh save`/`restore` script per spec (bin/omarchy-sesh).
- [x] Verify Hyprland 0.56 Lua dispatch API live; placement verified end-to-end (resize center-anchored → resize-then-move).
- [x] Ship systemd unit + autostart snippet.
- [ ] Decide save triggers: ExecStop on graphical-session.target vs power-menu wiring.
- [ ] Workspace→monitor remap on restore (monitor layout changes across reboots).
- [ ] Test `stableId` stability across compositor restarts as a match key.
- [ ] Wire into Omarchy provisioning (install/user/first-run/enable-user-units.sh + autostart.lua).
