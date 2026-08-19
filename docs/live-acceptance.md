# Live Acceptance

This procedure completes the live validation work tracked in
`docs/future-improvements.md`. Run it only in an intentional Omarchy/Hyprland
test session: the reboot, power-menu, and failed-launch cases affect the current
desktop. The evidence command itself is read-only.

## Reboot Restore And Service Health

1. Open a tiled terminal, a floating terminal with a distinctive size and
   position, a browser, and a multi-window application. Put them on at least two
   workspaces. Include a complete window group when testing Hyprland 0.56+.
2. Record each application's workspace. Record the floating window's `at` and
   `size` from `hyprctl -j clients` before reboot. For a nested dwindle layout,
   record all tiled rectangles as well.
3. Use the Omarchy **Reboot** power-menu action. Do not use a direct reboot
   command; this exercises the synchronous pre-shutdown snapshot.
4. After login and after applications settle, run:

   ```sh
   omarchy-sesh acceptance --expect-power-save
   ```

5. Confirm every line reports `PASS`. The command verifies the current
   compositor marker, source snapshot, restore service, autosave state, and
   one-to-one saved-window matching. `--expect-power-save` additionally proves
   that the restored source snapshot has the `logout` label.
6. Compare the recorded workspace and floating geometry with `hyprctl -j
   clients`. Confirm browser content using the browser's own restore behavior.
   For a complete nested dwindle layout, compare every recorded tiled rectangle
   and group member order. Tiled layout remains best-effort when its documented
   replay prerequisites are not met.

## Failure Recovery

Use an application launcher that can be made unavailable after its window has
been saved, without changing the saved database. Capture a normal desktop with
that application open through the Omarchy power menu, make its launcher exit
without creating a window, then reboot through the same menu.

After login, run:

```sh
omarchy-sesh acceptance --expect-restore-failure
```

The command expects the restore unit to be failed, the current-instance restore
marker to be incomplete, the autosave service to remain active but gated when
enabled, and the restore source to remain a complete snapshot. Also inspect
`omarchy-sesh status`: the failed restore must not replace that complete
snapshot. Restore the test application's launcher before the next normal reboot.

## Evidence To Retain

Record the command output, `systemctl --user status omarchy-sesh.service
omarchy-sesh-autosave.service`, and the pre/post `hyprctl -j clients` captures.
For monitor remapping or group testing, add monitor and group metadata to those
captures. Do not mark a roadmap item complete until the visual assertions and
the command evidence both pass.
