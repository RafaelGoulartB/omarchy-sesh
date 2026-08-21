import contextlib
import importlib.machinery
import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True


SCRIPT = Path(__file__).parents[1] / "bin" / "omarchy-sesh"
INSTALLER = Path(__file__).parents[1] / "install.sh"
UNINSTALLER = Path(__file__).parents[1] / "uninstall.sh"
AUTOSAVE_SERVICE = (
    Path(__file__).parents[1] / "systemd" / "user" / "omarchy-sesh-autosave.service"
)
RESTORE_SERVICE = (
    Path(__file__).parents[1] / "systemd" / "user" / "omarchy-sesh.service"
)


def load_module(state_home):
    with mock.patch.dict(
        os.environ,
        {"XDG_STATE_HOME": state_home, "XDG_CONFIG_HOME": state_home},
        clear=False,
    ):
        loader = importlib.machinery.SourceFileLoader("omarchy_sesh", str(SCRIPT))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module


def window(row_id, ord_, cls, pid, title="", initial_title=""):
    return {
        "id": row_id,
        "session": 1,
        "ord": ord_,
        "class": cls,
        "initial_class": cls,
        "title": title,
        "initial_title": initial_title,
        "cmdline": "/usr/bin/example",
        "cwd": "/tmp",
        "workspace_id": 1,
        "workspace_name": "1",
        "monitor_name": "DP-1",
        "monitor_description": "Dell Display",
        "at_x": None,
        "at_y": None,
        "size_w": None,
        "size_h": None,
        "floating": 0,
        "fullscreen": 0,
        "pinned": 0,
        "xwayland": 0,
        "pid": pid,
        "group_id": None,
        "group_ord": None,
    }


def tiled_client(row, address, at, size):
    return {
        "mapped": True,
        "address": address,
        "class": row["class"],
        "initialClass": row["initial_class"],
        "title": row["title"],
        "initialTitle": row["initial_title"],
        "workspace": {"id": row["workspace_id"]},
        "at": at,
        "size": size,
        "floating": False,
        "fullscreen": 0,
    }


def group_client(row, address, grouped):
    return {
        "mapped": True,
        "address": address,
        "class": row["class"],
        "initialClass": row["initial_class"],
        "title": row["title"],
        "initialTitle": row["initial_title"],
        "workspace": {"id": row["workspace_id"]},
        "grouped": grouped,
    }


def workspace_layout(workspace_id=1, width=1000, height=1000, complete=1):
    return {
        "workspace_id": workspace_id,
        "layout": "dwindle",
        "at_x": 0,
        "at_y": 0,
        "size_w": width,
        "size_h": height,
        "work_x": 0,
        "work_y": 0,
        "work_w": width,
        "work_h": height,
        "gap_top": 0,
        "gap_right": 0,
        "gap_bottom": 0,
        "gap_left": 0,
        "complete": complete,
    }


def live_workspace_context(workspace_id=1, width=1000, height=1000):
    return (
        [{"id": workspace_id, "monitor": "DP-1", "tiledLayout": "dwindle"}],
        [
            {
                "id": 0,
                "name": "DP-1",
                "x": 0,
                "y": 0,
                "width": width,
                "height": height,
                "scale": 1,
                "transform": 0,
                "reserved": [0, 0, 0, 0],
            }
        ],
    )


def layout_ipc_response(workspaces, monitors, clients=None, rules=None):
    def response(endpoint, *_args):
        if endpoint == "workspaces":
            return workspaces
        if endpoint == "monitors":
            return monitors
        if endpoint == "getoption":
            return {"css": "0 0 0 0"}
        if endpoint == "workspacerules":
            return rules or []
        if endpoint == "clients":
            return clients
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    return response


class OmarchySeshTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.module = load_module(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_config(self, content):
        self.module.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.module.CONFIG_PATH.write_text(content, encoding="utf-8")

    def assert_mode(self, path, expected):
        self.assertEqual(expected, path.stat().st_mode & 0o777, path)

    def test_runtime_state_is_owner_only_under_a_permissive_umask(self):
        previous_umask = os.umask(0)
        connection = None
        lock_file = None
        try:
            self.module.log("private")
            lock_file = self.module.acquire_operation_lock()
            connection = self.module.db_conn()
            with mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                clear=False,
            ):
                self.assertTrue(self.module.mark_restore_completed(1))

            self.assert_mode(self.module.STATE_DIR, 0o700)
            self.assert_mode(self.module.LOG_PATH.parent, 0o700)
            for path in (
                self.module.DB_PATH,
                Path(f"{self.module.DB_PATH}-wal"),
                Path(f"{self.module.DB_PATH}-shm"),
                self.module.LOCK_PATH,
                self.module.RESTORE_MARKER_PATH,
                self.module.LOG_PATH,
            ):
                self.assertTrue(path.exists(), path)
                self.assert_mode(path, 0o600)
        finally:
            if connection is not None:
                connection.close()
            if lock_file is not None:
                lock_file.close()
            os.umask(previous_umask)

    def test_existing_permissive_state_permissions_are_repaired(self):
        self.module.STATE_DIR.mkdir(mode=0o755, parents=True)
        self.module.LOG_PATH.parent.mkdir(mode=0o755)
        paths = (
            self.module.DB_PATH,
            Path(f"{self.module.DB_PATH}-wal"),
            Path(f"{self.module.DB_PATH}-shm"),
            Path(f"{self.module.DB_PATH}-journal"),
            self.module.LOCK_PATH,
            self.module.RESTORE_MARKER_PATH,
            self.module.LOG_PATH,
            self.module.STATE_DIR / "sesh-installed",
            self.module.STATE_DIR / "sesh-menu-created",
        )
        for path in paths:
            path.write_text("private")
            path.chmod(0o644)

        self.module.secure_state_storage()

        self.assert_mode(self.module.STATE_DIR, 0o700)
        self.assert_mode(self.module.LOG_PATH.parent, 0o700)
        for path in paths:
            self.assert_mode(path, 0o600)

    def test_symlinked_state_directory_is_rejected(self):
        target = Path(self.tempdir.name) / "target"
        target.mkdir()
        self.module.STATE_DIR.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(OSError, "unsafe state directory"):
            self.module.secure_state_storage()

    def test_config_defaults_preserve_existing_behavior(self):
        self.assertEqual(
            {
                "exclude_classes": [
                    "polkit-gnome-authentication-agent-1",
                    "xdg-desktop-portal",
                    "org.freedesktop.impl.portal.Polkit",
                ],
                "autosave_seconds": 60,
                "restore_timeout_seconds": 20.0,
                "snapshot_retention": 5,
                "monitor_fallback": "focused",
            },
            self.module.load_config(),
        )

    def test_config_accepts_the_complete_validated_surface(self):
        self.write_config(
            '{"exclude_classes": ["panel"], "autosave_seconds": 120, '
            '"restore_timeout_seconds": 45.5, "snapshot_retention": 8, '
            '"monitor_fallback": "DP-2"}'
        )

        self.assertEqual(
            {
                "exclude_classes": ["panel"],
                "autosave_seconds": 120,
                "restore_timeout_seconds": 45.5,
                "snapshot_retention": 8,
                "monitor_fallback": "DP-2",
            },
            self.module.load_config(),
        )

    def test_config_rejects_invalid_files_and_values(self):
        invalid_configs = {
            "invalid JSON": "{",
            "top-level value": "[]",
            "unknown setting": '{"restore_timout_seconds": 30}',
            "exclude_classes": '{"exclude_classes": [""]}',
            "autosave_seconds": '{"autosave_seconds": true}',
            "restore_timeout_seconds": '{"restore_timeout_seconds": 0}',
            "snapshot_retention": '{"snapshot_retention": 0}',
            "monitor_fallback": '{"monitor_fallback": ""}',
        }
        for expected, content in invalid_configs.items():
            with self.subTest(expected=expected):
                self.write_config(content)
                with self.assertRaisesRegex(self.module.ConfigError, expected):
                    self.module.load_config()

    def test_config_rejects_a_restore_timeout_below_one_group_retry(self):
        self.write_config(
            f'{{"restore_timeout_seconds": {self.module.RESTORE_RETRY_DELAY / 2}}}'
        )
        with self.assertRaisesRegex(
            self.module.ConfigError, "so a launch group can retry at least once"
        ):
            self.module.load_config()

    def test_config_rejects_monitor_fallbacks_that_name_no_policy_or_connector(self):
        for value in ("lowset", "Focused", "DP2", "-1"):
            with self.subTest(value=value):
                self.write_config(f'{{"monitor_fallback": "{value}"}}')
                with self.assertRaisesRegex(
                    self.module.ConfigError, "monitor_fallback must be"
                ):
                    self.module.load_config()

    def test_config_accepts_connector_shaped_monitor_fallbacks(self):
        for value in ("DP-2", "eDP-1", "HDMI-A-1", "DVI-D-1", "lowest"):
            with self.subTest(value=value):
                self.write_config(f'{{"monitor_fallback": "{value}"}}')
                self.assertEqual(value, self.module.load_config()["monitor_fallback"])

    def test_config_is_decoded_as_utf8_regardless_of_the_locale_encoding(self):
        config = mock.Mock()
        config.read_text.return_value = '{"exclude_classes": ["café"]}'
        with mock.patch.object(self.module, "CONFIG_PATH", config):
            self.assertEqual(["café"], self.module.load_config()["exclude_classes"])
        self.assertEqual({"encoding": "utf-8"}, config.read_text.call_args.kwargs)

    def test_unreadable_config_falls_back_to_defaults_instead_of_failing(self):
        config = mock.Mock()
        config.read_text.side_effect = PermissionError(13, "Permission denied")
        with (
            mock.patch.object(self.module, "CONFIG_PATH", config),
            mock.patch.object(self.module, "log") as logged,
        ):
            self.assertEqual(self.module.default_config(), self.module.load_config())
        self.assertIn("Permission denied", logged.call_args.args[0])

    def test_config_rejects_values_that_exceed_runtime_limits(self):
        for setting in (
            "autosave_seconds",
            "restore_timeout_seconds",
            "snapshot_retention",
        ):
            with self.subTest(setting=setting):
                self.write_config(f'{{"{setting}": {10**100}}}')
                with self.assertRaisesRegex(self.module.ConfigError, setting):
                    self.module.load_config()

    def test_config_rejects_non_utf8_input(self):
        self.module.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.module.CONFIG_PATH.write_bytes(b"\xff")
        with self.assertRaisesRegex(self.module.ConfigError, "must be UTF-8"):
            self.module.load_config()

    def test_invalid_config_still_captures_the_session_with_defaults(self):
        self.write_config('{"autosave_seconds": 0}')
        with (
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=None
            ) as acquire,
            mock.patch.object(self.module, "log") as logged,
        ):
            self.assertEqual(75, self.module.cmd_save(label="logout"))

        acquire.assert_called_once()
        reported = logged.call_args_list[0].args[0]
        self.assertIn("save[logout]", reported)
        self.assertIn("autosave_seconds", reported)
        self.assertIn("default settings", reported)

    def test_defaults_replace_a_rejected_config_on_the_capture_path(self):
        self.write_config('{"monitor_fallback": "lowset", "snapshot_retention": 8}')
        with mock.patch.object(self.module, "log"):
            self.assertEqual(
                self.module.default_config(),
                self.module.load_config_or_defaults("save[periodic]"),
            )

    def test_main_reports_configuration_errors_with_exit_status_two(self):
        self.write_config('{"snapshot_retention": 0}')
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), "restore"]),
            mock.patch.object(self.module, "acquire_operation_lock") as acquire,
            mock.patch("sys.stderr", stderr),
        ):
            result = self.module.main()

        self.assertEqual(2, result)
        self.assertIn("configuration error: snapshot_retention", stderr.getvalue())
        acquire.assert_not_called()

    def test_restore_service_does_not_restart_configuration_errors(self):
        self.assertIn("RestartPreventExitStatus=1 2", RESTORE_SERVICE.read_text())

    def test_autosave_service_restarts_on_failure_without_status_exemptions(self):
        unit = AUTOSAVE_SERVICE.read_text()
        self.assertIn("Restart=on-failure", unit)
        self.assertNotIn("RestartPreventExitStatus", unit)

    def test_services_use_an_owner_only_umask(self):
        self.assertIn("UMask=0077", RESTORE_SERVICE.read_text())
        self.assertIn("UMask=0077", AUTOSAVE_SERVICE.read_text())

    def test_lua_quote_uses_collision_free_long_string(self):
        value = "run 'quoted' ]] and ]=] command"
        quoted = self.module.lua_quote(value)
        self.assertTrue(quoted.startswith("[==["))
        self.assertTrue(quoted.endswith("]==]"))
        self.assertIn(value, quoted)

    def test_eval_lua_requires_exact_ok_response(self):
        result = mock.Mock(returncode=0, stdout="eval unavailable\n", stderr="")
        with (
            mock.patch.object(self.module.subprocess, "run", return_value=result),
            mock.patch.object(self.module, "log"),
        ):
            self.assertFalse(self.module.eval_lua("return true"))

        result.stdout = "ok\n"
        with mock.patch.object(self.module.subprocess, "run", return_value=result):
            self.assertTrue(self.module.eval_lua("return true"))

        result.returncode = 1
        result.stdout = ""
        with (
            mock.patch.object(self.module.subprocess, "run", return_value=result),
            mock.patch.object(self.module, "log"),
        ):
            self.assertIsNone(self.module.eval_lua("return true"))

        result.returncode = -9
        with (
            mock.patch.object(self.module.subprocess, "run", return_value=result),
            mock.patch.object(self.module, "log"),
        ):
            self.assertIsNone(self.module.eval_lua("return true"))

    def patch_animation_ipc(self, reported, set_result=True):
        """Patch the animation IPC helpers for the rest of the current test.

        These tests assert on calls made after the suppression block exits, so
        the patches must outlive it rather than wrap it.
        """
        self.start_patch("animations_enabled", return_value=reported)
        setter = self.start_patch("set_animations_enabled", return_value=set_result)
        self.start_patch("log")
        return setter

    def start_patch(self, target, **kwargs):
        patcher = mock.patch.object(self.module, target, **kwargs)
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def test_animations_are_suppressed_and_restored_around_placement(self):
        setter = self.patch_animation_ipc(True)
        with self.module.animations_disabled():
            self.assertEqual([mock.call(False)], setter.call_args_list)

        self.assertEqual([mock.call(False), mock.call(True)], setter.call_args_list)

    def test_animations_are_restored_when_placement_raises(self):
        setter = self.patch_animation_ipc(True)
        with self.assertRaises(RuntimeError), self.module.animations_disabled():
            raise RuntimeError("placement failed")

        self.assertEqual(mock.call(True), setter.call_args_list[-1])

    def test_animations_already_off_are_left_untouched(self):
        setter = self.patch_animation_ipc(False)
        with self.module.animations_disabled():
            pass
        setter.assert_not_called()

    def test_unreadable_animation_option_leaves_the_setting_untouched(self):
        setter = self.patch_animation_ipc(None)
        with self.module.animations_disabled():
            pass
        setter.assert_not_called()

    def test_failed_suppression_is_not_reverted(self):
        setter = self.patch_animation_ipc(True, set_result=False)
        with self.module.animations_disabled():
            pass

        self.assertEqual([mock.call(False)], setter.call_args_list)

    def test_animations_are_toggled_through_the_lua_config_api(self):
        # `hyprctl keyword animations:enabled` is answered with "keyword can't
        # work with non-legacy parsers" and still exits 0 on Hyprland 0.56, so
        # the write must go through hl.config and be read back.
        for enabled, expected in ((False, "false"), (True, "true")):
            with mock.patch.object(
                self.module, "eval_lua", return_value=True
            ) as evaluate:
                self.assertTrue(self.module.set_animations_enabled(enabled))
            script = evaluate.call_args.args[0]
            self.assertIn(
                f"hl.config({{ animations = {{ enabled = {expected} }} }})", script
            )
            self.assertIn(
                f"if hl.get_config('animations:enabled') ~= {expected} then", script
            )

    def test_animation_toggle_failure_is_reported(self):
        for result in (False, None):
            with mock.patch.object(self.module, "eval_lua", return_value=result):
                self.assertFalse(self.module.set_animations_enabled(False))

    def test_animations_enabled_reads_bool_and_int_option_shapes(self):
        for option, expected in (
            ({"option": "animations:enabled", "bool": True}, True),
            ({"option": "animations:enabled", "bool": False}, False),
            ({"option": "animations:enabled", "int": 0}, False),
            ({"option": "animations:enabled"}, None),
            ([], None),
            (None, None),
        ):
            with mock.patch.object(
                self.module, "hyprctl_json", return_value=option
            ) as hyprctl:
                self.assertIs(expected, self.module.animations_enabled())
            hyprctl.assert_called_once_with("getoption", "animations:enabled")

    def test_hyprctl_batch_json_parses_each_response(self):
        result = mock.Mock(
            returncode=0,
            stdout='{"bool": true}\n\n{"bool": false}\n',
            stderr="",
        )
        with mock.patch.object(
            self.module.subprocess, "run", return_value=result
        ) as run:
            self.assertEqual(
                [{"bool": True}, {"bool": False}],
                self.module.hyprctl_batch_json("getoption one", "getoption two"),
            )
        run.assert_called_once_with(
            ["hyprctl", "-j", "--batch", "getoption one; getoption two"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_migration_marks_historical_empty_session_unknown(self):
        self.module.STATE_DIR.mkdir(parents=True)
        conn = sqlite3.connect(self.module.DB_PATH)
        conn.executescript(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                label TEXT
            );
            CREATE TABLE windows (
                id INTEGER PRIMARY KEY, session INTEGER NOT NULL, ord INTEGER NOT NULL,
                class TEXT NOT NULL, initial_class TEXT, title TEXT, initial_title TEXT,
                cmdline TEXT NOT NULL, cwd TEXT, workspace_id INTEGER,
                workspace_name TEXT, monitor_name TEXT, at_x INTEGER, at_y INTEGER,
                size_w INTEGER, size_h INTEGER, floating INTEGER NOT NULL DEFAULT 0,
                fullscreen INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
                xwayland INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO sessions (id, label) VALUES (1, 'legacy');
            INSERT INTO sessions (id, label) VALUES (2, 'populated');
            INSERT INTO windows (
                id, session, ord, class, cmdline, floating, fullscreen, pinned,
                xwayland
            ) VALUES (1, 2, 0, 'terminal', '/usr/bin/terminal', 0, 0, 0, 0);
            """
        )
        conn.close()

        conn = self.module.db_conn()
        status = conn.execute(
            "SELECT capture_status FROM sessions WHERE id = 1"
        ).fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(windows)")}
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        migrated = conn.execute(
            "SELECT class, group_id, group_ord FROM windows WHERE id = 1"
        ).fetchone()
        conn.close()

        self.assertEqual("legacy_unknown", status)
        self.assertIn("pid", columns)
        self.assertIn("monitor_description", columns)
        self.assertIn("group_id", columns)
        self.assertIn("group_ord", columns)
        self.assertIn("workspace_layouts", tables)
        self.assertIn("named_sessions", tables)
        self.assertEqual(6, version)
        self.assertEqual(("terminal", None, None), migrated)

    def test_named_snapshot_is_retained_and_excluded_from_default_restore(self):
        named_id = self.module.persist_snapshot(
            "manual", "complete", "", [window(1, 0, "named", 1)], name="work"
        )
        automatic_ids = [
            self.module.persist_snapshot(
                "periodic", "complete", "", [window(index, 0, f"app-{index}", index)]
            )
            for index in range(2, 8)
        ]

        conn = self.module.db_conn()
        self.assertEqual(
            (named_id, "manual", mock.ANY), self.module.named_session(conn, "work")
        )
        self.assertEqual(automatic_ids[-1], self.module.latest_session(conn)[0])
        retained = {row[0] for row in conn.execute("SELECT id FROM sessions")}
        conn.close()

        self.assertIn(named_id, retained)
        self.assertNotIn(automatic_ids[0], retained)
        self.assertEqual(6, len(retained))

    def test_named_snapshot_rejects_an_existing_name(self):
        self.module.persist_snapshot("manual", "complete", "", [], name="work")

        with self.assertRaisesRegex(self.module.NamedSessionConflict, "work"):
            self.module.persist_snapshot("manual", "complete", "", [], name="work")

    def test_list_prints_named_sessions(self):
        sid = self.module.persist_snapshot(
            "manual", "complete", "", [window(1, 0, "terminal", 1)], name="work"
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(0, self.module.cmd_list())

        self.assertIn(f"work: session {sid}", stdout.getvalue())
        self.assertIn("1 windows", stdout.getvalue())

    def test_list_json_prints_named_session_metadata(self):
        sid = self.module.persist_snapshot(
            "manual", "complete", "", [window(1, 0, "terminal", 1)], name='work "desk"'
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(0, self.module.cmd_list(as_json=True))

        self.assertEqual(
            [
                {
                    "name": 'work "desk"',
                    "id": sid,
                    "created_at": mock.ANY,
                    "windows": 1,
                }
            ],
            self.module.json.loads(stdout.getvalue()),
        )

    def test_list_json_prints_an_empty_array_without_named_sessions(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(0, self.module.cmd_list(as_json=True))

        self.assertEqual([], self.module.json.loads(stdout.getvalue()))

    def test_delete_removes_named_snapshot_and_its_payload(self):
        sid = self.module.persist_snapshot(
            "manual",
            "complete",
            "",
            [window(1, 0, "terminal", 1)],
            [workspace_layout()],
            name="work",
        )

        self.assertEqual(0, self.module.cmd_delete("work"))
        conn = self.module.db_conn()
        self.assertIsNone(self.module.named_session(conn, "work"))
        self.assertIsNone(
            conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone()
        )
        self.assertIsNone(
            conn.execute("SELECT 1 FROM windows WHERE session = ?", (sid,)).fetchone()
        )
        self.assertIsNone(
            conn.execute(
                "SELECT 1 FROM workspace_layouts WHERE session = ?", (sid,)
            ).fetchone()
        )
        conn.close()

    def test_delete_rejects_a_missing_named_session(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(1, self.module.cmd_delete("missing"))
        self.assertIn("no saved session named 'missing'", stderr.getvalue())

    def test_restore_selects_the_requested_named_snapshot(self):
        sid = self.module.persist_snapshot(
            "manual", "complete", "", [window(1, 0, "terminal", 1)], name="work"
        )
        lock_file = mock.Mock()
        with (
            mock.patch.dict(
                os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}, clear=False
            ),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=[]),
            mock.patch.object(self.module, "restore_was_completed", return_value=False),
            mock.patch.object(
                self.module, "restore_windows", return_value=(1, 0, 1, False)
            ) as restore_windows,
            mock.patch.object(self.module, "mark_restore_completed", return_value=True),
        ):
            self.assertEqual(0, self.module.cmd_restore(name="work"))

        self.assertEqual(sid, restore_windows.call_args.args[0][0]["session"])

    def test_named_restore_can_repeat_in_the_same_desktop(self):
        self.module.persist_snapshot(
            "manual", "complete", "", [window(1, 0, "terminal", 1)], name="work"
        )
        lock_file = mock.Mock()
        with (
            mock.patch.dict(
                os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}, clear=False
            ),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=[]),
            mock.patch.object(
                self.module, "restore_was_completed", return_value=True
            ) as restore_was_completed,
            mock.patch.object(
                self.module, "restore_windows", return_value=(1, 0, 1, False)
            ) as restore_windows,
            mock.patch.object(self.module, "mark_restore_completed", return_value=True),
        ):
            self.assertEqual(0, self.module.cmd_restore(name="work"))

        restore_was_completed.assert_not_called()
        restore_windows.assert_called_once()

    def test_restore_rejects_a_missing_named_snapshot(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(1, self.module.cmd_restore(name="missing"))
        self.assertIn("no saved session named 'missing'", stderr.getvalue())

    def test_empty_xdg_paths_migrate_relative_state_and_config(self):
        legacy = Path(self.tempdir.name) / "legacy" / "omarchy"
        legacy.mkdir(parents=True)
        (legacy / "session.db").write_text("database")
        (legacy / "sesh").mkdir()
        (legacy / "sesh" / "config.json").write_text("{}")
        with (
            mock.patch.dict(
                os.environ,
                {"XDG_STATE_HOME": "", "XDG_CONFIG_HOME": ""},
                clear=False,
            ),
            mock.patch.object(
                self.module, "STATE_DIR", Path(self.tempdir.name) / "state" / "omarchy"
            ),
            mock.patch.object(
                self.module,
                "CONFIG_PATH",
                Path(self.tempdir.name) / "config" / "omarchy" / "sesh" / "config.json",
            ),
            mock.patch.object(self.module, "Path", wraps=Path) as path_class,
        ):
            path_class.return_value = legacy
            self.module.migrate_empty_xdg_paths()
        self.assertEqual(
            "database",
            (Path(self.tempdir.name) / "state" / "omarchy" / "session.db").read_text(),
        )
        self.assert_mode(
            Path(self.tempdir.name) / "state" / "omarchy" / "session.db", 0o600
        )
        self.assertTrue(
            (
                Path(self.tempdir.name) / "config" / "omarchy" / "sesh" / "config.json"
            ).exists()
        )

    def test_complete_empty_snapshot_supersedes_older_nonempty_snapshot(self):
        row = window(1, 0, "terminal", 10)
        self.module.persist_snapshot("periodic", "complete", "", [row])
        empty_id = self.module.persist_snapshot("manual", "complete", "", [])

        conn = self.module.db_conn()
        session = self.module.latest_session(conn)
        rows = self.module.load_windows(conn, session[0])
        conn.close()

        self.assertEqual(empty_id, session[0])
        self.assertEqual([], rows)

    def test_failed_teardown_does_not_supersede_complete_snapshot(self):
        complete_id = self.module.persist_snapshot(
            "periodic", "complete", "", [window(1, 0, "terminal", 10)]
        )
        self.module.persist_snapshot(
            "logout", "failed", "teardown captures are diagnostic only", []
        )

        conn = self.module.db_conn()
        session = self.module.latest_session(conn)
        conn.close()

        self.assertEqual(complete_id, session[0])

    def test_group_metadata_persists_round_trip(self):
        row = window(1, 0, "terminal", 10)
        row.update(group_id=1, group_ord=0)
        sid = self.module.persist_snapshot("periodic", "complete", "", [row])
        conn = self.module.db_conn()
        loaded = self.module.load_windows(conn, sid)
        conn.close()
        self.assertEqual((1, 0), (loaded[0]["group_id"], loaded[0]["group_ord"]))

    def test_workspace_layout_metadata_persists_round_trip(self):
        layout = workspace_layout()
        sid = self.module.persist_snapshot(
            "periodic", "complete", "", [window(1, 0, "terminal", 10)], [layout]
        )
        conn = self.module.db_conn()
        loaded = self.module.load_workspace_layouts(conn, sid)
        conn.close()
        self.assertEqual(layout, loaded[1])

    def test_pruning_removes_workspace_layout_metadata(self):
        session_ids = []
        for index in range(6):
            session_ids.append(
                self.module.persist_snapshot(
                    "periodic",
                    "complete",
                    "",
                    [window(index + 1, 0, f"app-{index}", index)],
                    [workspace_layout()],
                )
            )
        conn = self.module.db_conn()
        retained = {
            row[0] for row in conn.execute("SELECT session FROM workspace_layouts")
        }
        conn.close()
        self.assertNotIn(session_ids[0], retained)
        self.assertEqual(set(session_ids[1:]), retained)

    def test_snapshot_retention_applies_to_complete_and_diagnostic_history(self):
        for index in range(3):
            self.module.persist_snapshot(
                "periodic", "complete", "", [], snapshot_retention=2
            )
            self.module.persist_snapshot(
                "logout", "failed", f"failure {index}", [], snapshot_retention=2
            )
        conn = self.module.db_conn()
        counts = dict(
            conn.execute(
                "SELECT capture_status, COUNT(*) FROM sessions GROUP BY capture_status"
            )
        )
        conn.close()

        self.assertEqual({"complete": 2, "failed": 2}, counts)

    def test_manual_snapshot_opens_autosave_gate(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}):
            self.assertEqual(
                0, self.module._save_clients("manual", [], [], "complete", [])
            )
            self.assertTrue(self.module.restore_is_ready())

    def test_named_save_prints_a_success_message(self):
        stdout = io.StringIO()
        with (
            mock.patch.dict(
                os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}, clear=False
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(
                0,
                self.module._save_clients(
                    "manual", [], [], "complete", [], name="test"
                ),
            )

        self.assertIn("Session saved under test", stdout.getvalue())
        conn = self.module.db_conn()
        self.assertIsNotNone(self.module.named_session(conn, "test"))
        conn.close()

    def test_save_reports_hyprland_ipc_failure(self):
        stderr = io.StringIO()
        with (
            mock.patch.object(self.module, "hyprctl_json", return_value=None),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(1, self.module.cmd_save(name="test"))
        self.assertIn("save failed: Hyprland IPC is unavailable", stderr.getvalue())

    def test_logout_save_closes_autosave_gate_before_capture(self):
        responses = {
            "clients": [],
            "monitors": [],
            "workspaces": [],
            "getoption": {"css": "0"},
            "workspacerules": [],
        }
        with (
            mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}),
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=lambda endpoint, *_: responses[endpoint],
            ),
        ):
            self.module.mark_restore_completed(7)
            self.assertEqual(0, self.module.cmd_save("logout"))
            self.assertFalse(self.module.restore_is_ready())

    def test_logout_save_requires_autosave_gate(self):
        lock_file = mock.Mock()
        with (
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(
                self.module, "mark_restore_completed", return_value=False
            ),
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
        ):
            self.assertEqual(75, self.module.cmd_save("logout"))
        hyprctl.assert_not_called()
        lock_file.close.assert_called_once_with()

    def test_periodic_save_rechecks_autosave_gate_under_lock(self):
        lock_file = mock.Mock()
        with (
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "restore_is_ready", return_value=False),
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
        ):
            self.assertEqual(75, self.module.cmd_save("periodic"))
        hyprctl.assert_not_called()
        lock_file.close.assert_called_once_with()

    def test_save_captures_tiled_geometry_as_slot_metadata(self):
        client = {
            "mapped": True,
            "address": "0x1",
            "class": "terminal",
            "initialClass": "terminal",
            "title": "Terminal",
            "initialTitle": "Terminal",
            "pid": 10,
            "workspace": {"id": 1, "name": "1"},
            "monitor": 0,
            "at": [12, 34],
            "size": [800, 600],
            "floating": False,
        }
        with (
            mock.patch.object(
                self.module, "read_proc", return_value=("/usr/bin/terminal", "/tmp", "")
            ),
            mock.patch.object(
                self.module, "persist_snapshot", return_value=7
            ) as persist,
            mock.patch.object(self.module, "log"),
        ):
            self.assertEqual(
                0,
                self.module._save_clients(
                    "periodic",
                    [client],
                    [
                        {
                            "id": 0,
                            "name": "DP-1",
                            "description": "Dell Display",
                            "x": 0,
                            "y": 0,
                            "width": 1000,
                            "height": 1000,
                            "scale": 1,
                            "transform": 0,
                            "reserved": [0, 0, 0, 0],
                        }
                    ],
                    "complete",
                    [],
                    workspaces=[{"id": 1, "tiledLayout": "dwindle"}],
                    gaps_out=[0, 0, 0, 0],
                    gaps_in=[0, 0, 0, 0],
                ),
            )

        saved = persist.call_args.args[3][0]
        self.assertEqual(
            (12, 34, 800, 600),
            (saved["at_x"], saved["at_y"], saved["size_w"], saved["size_h"]),
        )
        self.assertEqual(
            ("DP-1", "Dell Display"),
            (saved["monitor_name"], saved["monitor_description"]),
        )

    def test_save_captures_complete_nested_workspace_metadata(self):
        clients = []
        geometries = ((0, 0, 300, 1000), (300, 0, 700, 500), (300, 500, 700, 500))
        for index, geometry in enumerate(geometries, start=1):
            clients.append(
                {
                    "mapped": True,
                    "address": f"0x{index}",
                    "class": f"app-{index}",
                    "pid": index,
                    "workspace": {"id": 1, "name": "1"},
                    "monitor": 0,
                    "at": list(geometry[:2]),
                    "size": list(geometry[2:]),
                    "floating": False,
                }
            )
        with (
            mock.patch.object(
                self.module, "read_proc", return_value=("/usr/bin/example", "/tmp", "")
            ),
            mock.patch.object(
                self.module, "persist_snapshot", return_value=7
            ) as persist,
            mock.patch.object(self.module, "log"),
        ):
            self.assertEqual(
                0,
                self.module._save_clients(
                    "periodic",
                    clients,
                    [
                        dict(
                            live_workspace_context()[1][0],
                            description="Dell Display",
                        )
                    ],
                    "complete",
                    [],
                    workspaces=[{"id": 1, "tiledLayout": "dwindle"}],
                    gaps_out=[0, 0, 0, 0],
                    gaps_in=[0, 0, 0, 0],
                ),
            )

        self.assertEqual([workspace_layout()], persist.call_args.args[4])

    def test_workspace_layout_is_incomplete_when_tiled_client_is_not_captured(self):
        clients = [
            {
                "mapped": True,
                "address": "0xa",
                "workspace": {"id": 1},
                "monitor": 0,
                "floating": False,
            },
            {
                "mapped": True,
                "address": "0xb",
                "workspace": {"id": 1},
                "monitor": 0,
                "floating": False,
            },
            {
                "mapped": True,
                "address": "0xexcluded",
                "workspace": {"id": 1},
                "monitor": 0,
                "floating": False,
            },
        ]
        first = window(1, 0, "first", 10)
        second = window(2, 1, "second", 20)
        first.update(at_x=0, at_y=0, size_w=500, size_h=1000)
        second.update(at_x=500, at_y=0, size_w=500, size_h=1000)
        layouts = self.module.captured_workspace_layouts(
            clients,
            [(first, "0xa", []), (second, "0xb", [])],
            live_workspace_context()[1],
            [{"id": 1, "tiledLayout": "dwindle"}],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        )
        self.assertEqual(0, layouts[0]["complete"])

    def test_save_captures_complete_group_membership_and_order(self):
        clients = []
        for address, title in (("0xa", "A"), ("0xb", "B")):
            clients.append(
                {
                    "mapped": True,
                    "address": address,
                    "class": "terminal",
                    "initialClass": "terminal",
                    "title": title,
                    "initialTitle": title,
                    "pid": 10,
                    "workspace": {"id": 1, "name": "1"},
                    "monitor": 0,
                    "at": [0, 0],
                    "size": [1000, 1000],
                    "floating": False,
                    "grouped": ["0xb", "0xa"],
                }
            )
        with (
            mock.patch.object(
                self.module, "read_proc", return_value=("/usr/bin/terminal", "/tmp", "")
            ),
            mock.patch.object(
                self.module, "persist_snapshot", return_value=7
            ) as persist,
            mock.patch.object(self.module, "log"),
        ):
            self.assertEqual(
                0,
                self.module._save_clients(
                    "periodic",
                    clients,
                    [{"id": 0, "name": "DP-1", "description": "Dell Display"}],
                    "complete",
                    [],
                ),
            )

        saved = persist.call_args.args[3]
        self.assertEqual(
            [(1, 1), (1, 0)], [(r["group_id"], r["group_ord"]) for r in saved]
        )

    def test_save_drops_incomplete_group_metadata(self):
        first = {"class": "terminal"}
        self.module.assign_snapshot_groups([(first, "0xa", ["0xa", "0xb"])])
        self.assertEqual((None, None), (first["group_id"], first["group_ord"]))

    def test_process_groups_share_pid_but_not_class(self):
        rows = [
            window(1, 0, "chromium", 10),
            window(2, 1, "slack-webapp", 10),
            window(3, 2, "terminal", 20),
            window(4, 3, "terminal", 30),
        ]
        groups = self.module.process_groups(rows)
        self.assertEqual([2, 1, 1], [len(group) for group in groups])

    def test_monitor_targets_prefer_name_then_unique_description(self):
        exact = window(1, 0, "terminal", 10)
        renamed = window(2, 1, "browser", 20)
        renamed.update(
            workspace_id=2,
            monitor_name="DP-OLD",
            monitor_description="LG UltraFine",
        )
        monitors = [
            {"id": 0, "name": "DP-1", "description": "Dell Display"},
            {
                "id": 1,
                "name": "HDMI-A-1",
                "description": "LG UltraFine",
                "focused": True,
            },
        ]

        self.assertEqual(
            {1: "DP-1", 2: "HDMI-A-1"},
            self.module.workspace_monitor_targets([exact, renamed], monitors),
        )

    def test_monitor_target_uses_description_when_displays_swap_connectors(self):
        row = window(1, 0, "terminal", 10)
        monitors = [
            {"id": 0, "name": "DP-1", "description": "Other Display"},
            {"id": 1, "name": "DP-2", "description": "Dell Display"},
        ]

        self.assertEqual(
            {1: "DP-2"}, self.module.workspace_monitor_targets([row], monitors)
        )

    def test_ambiguous_description_mismatch_uses_fallback(self):
        row = window(1, 0, "terminal", 10)
        monitors = [
            {"id": 0, "name": "eDP-1", "description": "Laptop", "focused": True},
            {"id": 1, "name": "DP-1", "description": "Other Display"},
            {"id": 2, "name": "DP-2", "description": "Dell Display"},
            {"id": 3, "name": "DP-3", "description": "Dell Display"},
        ]

        self.assertEqual(
            {1: "eDP-1"}, self.module.workspace_monitor_targets([row], monitors)
        )

    def test_monitor_target_uses_focused_fallback_for_disconnected_display(self):
        row = window(1, 0, "terminal", 10)
        monitors = [
            {"id": 0, "name": "DP-2", "description": "Other"},
            {"id": 1, "name": "eDP-1", "description": "Laptop", "focused": True},
        ]

        self.assertEqual(
            {1: "eDP-1"}, self.module.workspace_monitor_targets([row], monitors)
        )

    def test_monitor_target_fallback_is_deterministic_without_focus(self):
        row = window(1, 0, "terminal", 10)
        monitors = [
            {"id": 4, "name": "DP-4"},
            {"id": 2, "name": "DP-2"},
        ]

        self.assertEqual(
            {1: "DP-2"}, self.module.workspace_monitor_targets([row], monitors)
        )

    def test_monitor_target_can_use_lowest_or_preferred_fallback(self):
        row = window(1, 0, "terminal", 10)
        monitors = [
            {"id": 4, "name": "DP-4", "focused": True},
            {"id": 2, "name": "DP-2"},
        ]

        self.assertEqual(
            {1: "DP-2"},
            self.module.workspace_monitor_targets([row], monitors, "lowest"),
        )
        self.assertEqual(
            {1: "DP-2"},
            self.module.workspace_monitor_targets([row], monitors, "DP-2"),
        )

    def test_unavailable_preferred_monitor_falls_back_safely(self):
        row = window(1, 0, "terminal", 10)
        monitors = [
            {"id": 0, "name": "eDP-1", "focused": True},
            {"id": 1, "name": "DP-1"},
        ]
        with mock.patch.object(self.module, "log") as log:
            targets = self.module.workspace_monitor_targets([row], monitors, "HDMI-A-1")

        self.assertEqual({1: "eDP-1"}, targets)
        log.assert_called_once_with(
            "restore: configured fallback monitor 'HDMI-A-1' is unavailable"
        )

    def test_monitor_target_skips_conflicting_workspace_identity(self):
        first = window(1, 0, "terminal", 10)
        second = window(2, 1, "browser", 20)
        second["monitor_name"] = "DP-2"
        with mock.patch.object(self.module, "log") as log:
            targets = self.module.workspace_monitor_targets(
                [first, second],
                [{"id": 0, "name": "DP-1"}, {"id": 1, "name": "DP-2"}],
            )

        self.assertEqual({}, targets)
        log.assert_called_once()

    def test_floating_placement_resizes_before_moving_in_one_evaluation(self):
        row = window(1, 0, "terminal", 10)
        row.update(floating=1, at_x=100, at_y=200, size_w=800, size_h=600)
        with (
            mock.patch.object(self.module, "eval_lua", return_value=True) as evaluate,
            mock.patch.object(self.module, "dispatch") as dispatch,
        ):
            self.assertTrue(self.module.place_window(row, "0x1"))

        dispatch.assert_not_called()
        evaluate.assert_called_once()
        script = evaluate.call_args.args[0]
        self.assertLess(
            script.index("hl.dsp.window.resize"), script.index("x = 100, y = 200")
        )
        self.assertIn("hl.dsp.window.move({ workspace = 1", script)
        self.assertIn('hl.dsp.window.float({ action = "on"', script)
        self.assertIn("failed=failed+1", script)

    def test_placement_reports_failure_without_aborting_remaining_dispatches(self):
        row = window(1, 0, "terminal", 10)
        with mock.patch.object(self.module, "eval_lua", return_value=False) as evaluate:
            self.assertFalse(self.module.place_window(row, "0x1"))

        script = evaluate.call_args.args[0]
        # Every dispatch is counted rather than raising at the first failure,
        # so one failed property cannot skip the rest of the placement.
        self.assertNotIn("assert(", script)
        self.assertEqual(1, script.count("if failed>0 then error"))

    def test_placement_ipc_failure_is_reported_as_failure(self):
        row = window(1, 0, "terminal", 10)
        with mock.patch.object(self.module, "eval_lua", return_value=None):
            self.assertFalse(self.module.place_window(row, "0x1"))

    def test_already_correct_window_costs_no_compositor_round_trip(self):
        row = window(1, 0, "terminal", 10)
        current = {
            "workspace": {"id": 1},
            "floating": False,
            "fullscreen": 0,
            "fullscreenClient": 0,
            "pinned": False,
        }
        with (
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "dispatch") as dispatch,
        ):
            self.assertTrue(self.module.place_window(row, "0x1", current))

        evaluate.assert_not_called()
        dispatch.assert_not_called()

    def test_fullscreen_is_cleared_before_floating_state_changes(self):
        row = window(1, 0, "terminal", 10)
        row.update(floating=1, fullscreen=2)
        current = {
            "workspace": {"id": 1},
            "floating": False,
            "fullscreen": 2,
            "fullscreenClient": 2,
            "pinned": False,
        }
        operations = self.module.window_placement_operations(row, "0x1", current)
        self.assertEqual(
            [
                (
                    "hl.dsp.window.fullscreen_state({ internal = 0, client = 0, "
                    "window = [[address:0x1]] })"
                ),
                'hl.dsp.window.float({ action = "on", window = [[address:0x1]] })',
                (
                    "hl.dsp.window.fullscreen_state({ internal = 2, client = 2, "
                    "window = [[address:0x1]] })"
                ),
            ],
            operations,
        )

    def test_pin_is_temporarily_cleared_before_floating_geometry(self):
        row = window(1, 0, "terminal", 10)
        row.update(floating=1, pinned=1, at_x=100, at_y=200, size_w=800, size_h=600)
        current = {
            "workspace": {"id": 1},
            "at": [0, 0],
            "size": [400, 300],
            "floating": True,
            "fullscreen": 0,
            "fullscreenClient": 0,
            "pinned": True,
        }

        operations = self.module.window_placement_operations(row, "0x1", current)

        self.assertEqual(2, sum("window.pin" in operation for operation in operations))
        self.assertLess(
            operations.index("hl.dsp.window.pin({ window = [[address:0x1]] })"),
            next(
                i
                for i, operation in enumerate(operations)
                if "window.resize" in operation
            ),
        )
        self.assertEqual(
            "hl.dsp.window.pin({ window = [[address:0x1]] })", operations[-1]
        )

    def test_matching_floating_geometry_is_not_redispatched(self):
        row = window(1, 0, "terminal", 10)
        row.update(floating=1, at_x=100, at_y=200, size_w=800, size_h=600)
        current = {
            "workspace": {"id": 1},
            "at": [100, 200],
            "size": [800, 600],
            "floating": True,
            "fullscreen": 0,
            "fullscreenClient": 0,
            "pinned": False,
        }

        self.assertEqual(
            [], self.module.window_placement_operations(row, "0x1", current)
        )

    def test_divergent_client_fullscreen_state_is_still_corrected(self):
        row = window(1, 0, "terminal", 10)
        current = {
            "workspace": {"id": 1},
            "floating": False,
            "fullscreen": 0,
            "fullscreenClient": 1,
            "pinned": False,
        }
        operations = self.module.window_placement_operations(row, "0x1", current)
        self.assertEqual(
            [
                (
                    "hl.dsp.window.fullscreen_state({ internal = 0, client = 0, "
                    "window = [[address:0x1]] })"
                )
            ],
            operations,
        )

    def test_unknown_client_state_still_dispatches_every_property(self):
        row = window(1, 0, "terminal", 10)
        row.update(pinned=1)
        operations = self.module.window_placement_operations(row, "0x1")
        self.assertEqual(
            [
                (
                    "hl.dsp.window.fullscreen_state({ internal = 0, client = 0, "
                    "window = [[address:0x1]] })"
                ),
                (
                    "hl.dsp.window.move({ workspace = 1, follow = false, "
                    "window = [[address:0x1]] })"
                ),
                'hl.dsp.window.float({ action = "off", window = [[address:0x1]] })',
                "hl.dsp.window.pin({ window = [[address:0x1]] })",
            ],
            operations,
        )

    def test_floating_verification_reapplies_mismatched_geometry(self):
        row = window(1, 0, "terminal", 10)
        row.update(floating=1, at_x=100, at_y=200, size_w=800, size_h=600)
        wrong = {
            "address": "0x1",
            "at": [0, 0],
            "size": [400, 300],
            "floating": True,
        }
        correct = dict(wrong, at=[100, 200], size=[800, 600])
        with (
            mock.patch.object(
                self.module, "hyprctl_json", side_effect=[[wrong], [correct]]
            ),
            mock.patch.object(self.module, "place_window", return_value=True) as place,
            mock.patch.object(self.module.time, "sleep"),
        ):
            result = self.module.verify_floating_placements([row], {1: "0x1"})

        self.assertTrue(result)
        place.assert_called_once_with(row, "0x1", wrong)

    def test_floating_verification_ipc_failure_is_retryable(self):
        row = window(1, 0, "terminal", 10)
        row.update(floating=1, at_x=100, at_y=200, size_w=800, size_h=600)
        with (
            mock.patch.object(self.module, "hyprctl_json", return_value=None),
            mock.patch.object(self.module, "place_window") as place,
            mock.patch.object(self.module.time, "sleep"),
        ):
            result = self.module.verify_floating_placements([row], {1: "0x1"})

        self.assertIsNone(result)
        place.assert_not_called()

    def test_floating_verification_reports_geometry_that_does_not_settle(self):
        row = window(1, 0, "terminal", 10)
        row.update(floating=1, at_x=100, at_y=200, size_w=800, size_h=600)
        wrong = {"address": "0x1", "at": [0, 0], "size": [400, 300]}
        with (
            mock.patch.object(
                self.module, "hyprctl_json", side_effect=[[wrong], [wrong]]
            ),
            mock.patch.object(self.module, "place_window", return_value=True),
            mock.patch.object(self.module.time, "sleep"),
            mock.patch.object(self.module, "log") as log,
        ):
            result = self.module.verify_floating_placements([row], {1: "0x1"})

        self.assertFalse(result)
        log.assert_called_once_with(
            "restore: floating geometry did not settle for terminal"
        )

    def test_placement_without_an_address_fails(self):
        with mock.patch.object(self.module, "eval_lua") as evaluate:
            self.assertFalse(self.module.place_window(window(1, 0, "terminal", 10), ""))
        evaluate.assert_not_called()

    def test_prepare_moves_each_workspace_once_before_window_state(self):
        rows = [window(1, 0, "terminal", 10), window(2, 1, "browser", 20)]
        prepared = set()
        with mock.patch.object(self.module, "dispatch", return_value=True) as dispatch:
            self.assertTrue(
                self.module.prepare_workspace_monitor(
                    rows[0], "0x1", {1: "DP-1"}, {1: "eDP-1"}, prepared
                )
            )
            self.assertTrue(
                self.module.prepare_workspace_monitor(
                    rows[1], "0x2", {1: "DP-1"}, {1: "eDP-1"}, prepared
                )
            )

        self.assertEqual(
            [
                "hl.dsp.window.move({ workspace = 1, follow = false, window = [[address:0x1]] })",
                "hl.dsp.workspace.move({ workspace = 1, monitor = [[DP-1]] })",
            ],
            [call.args[0] for call in dispatch.call_args_list],
        )
        self.assertEqual({1}, prepared)

    def test_prepare_skips_workspace_already_on_saved_monitor(self):
        row = window(1, 0, "terminal", 10)
        prepared = set()
        with mock.patch.object(self.module, "dispatch") as dispatch:
            self.assertTrue(
                self.module.prepare_workspace_monitor(
                    row, "0x1", {1: "DP-1"}, {1: "DP-1"}, prepared
                )
            )

        dispatch.assert_not_called()
        self.assertEqual({1}, prepared)

    def test_monitor_move_refreshes_client_before_placement(self):
        row = window(1, 0, "terminal", 10)
        row["floating"] = 1
        stale = {"mapped": True, "address": "0x1", "fullscreen": 2}
        refreshed = {"mapped": True, "address": "0x1", "fullscreen": 0}
        with (
            mock.patch.object(
                self.module,
                "load_workspace_monitor_context",
                return_value=({1: "DP-1"}, {1: "eDP-1"}, False),
            ),
            mock.patch.object(
                self.module, "prepare_workspace_monitor", return_value=True
            ),
            mock.patch.object(
                self.module, "hyprctl_json", return_value=[refreshed]
            ) as hyprctl,
            mock.patch.object(self.module, "place_window", return_value=True) as place,
            mock.patch.object(
                self.module, "verify_floating_placements", return_value=True
            ),
            mock.patch.object(self.module, "restore_window_groups", return_value=True),
            mock.patch.object(self.module, "load_focus_context", return_value={}),
            mock.patch.object(self.module, "restore_focus_context", return_value=True),
        ):
            result = self.module.restore_windows([row], {1: "0x1"}, [stale])

        self.assertEqual((1, 0, 0, False), result)
        hyprctl.assert_called_once_with("clients")
        place.assert_called_once_with(row, "0x1", refreshed)

    def test_monitor_move_failure_does_not_block_later_workspace(self):
        first = window(1, 0, "terminal", 10)
        second = window(2, 1, "browser", 20)
        second.update(
            workspace_id=2,
            monitor_name="DP-2",
            monitor_description="Second Display",
        )
        targets = {1: "DP-1", 2: "DP-2"}
        current = {1: "eDP-1", 2: "eDP-1"}
        prepared = set()
        with mock.patch.object(
            self.module, "dispatch", side_effect=[True, False, True, True]
        ) as dispatch:
            self.assertFalse(
                self.module.prepare_workspace_monitor(
                    first, "0x1", targets, current, prepared
                )
            )
            self.assertTrue(
                self.module.prepare_workspace_monitor(
                    second, "0x2", targets, current, prepared
                )
            )

        self.assertEqual(4, dispatch.call_count)
        self.assertEqual({2}, prepared)

    def test_monitor_remap_ipc_failure_is_retryable(self):
        with (
            mock.patch.object(self.module, "hyprctl_json", side_effect=[None, []]),
            mock.patch.object(self.module, "log"),
        ):
            self.assertEqual(
                ({}, {}, True),
                self.module.load_workspace_monitor_context(
                    [window(1, 0, "terminal", 10)]
                ),
            )

    def test_chromium_webapps_split_from_shared_browser_process(self):
        slack = window(
            1,
            0,
            "chrome-app.slack.com__client_team_channel-Default",
            10,
            initial_title="app.slack.com_/client/team/channel",
        )
        discord = window(
            2,
            1,
            "chrome-discord.com__channels_@me-Default",
            10,
            initial_title="discord.com_/channels/@me",
        )
        browser = window(3, 2, "chromium", 10)

        groups = self.module.process_groups([slack, discord, browser])

        self.assertEqual([[slack], [discord], [browser]], groups)
        self.assertEqual(
            "omarchy-launch-webapp https://app.slack.com/client/team/channel",
            self.module.launch_command(slack),
        )
        self.assertEqual(
            "omarchy-launch-webapp https://discord.com/channels/@me",
            self.module.launch_command(discord),
        )

    def test_normal_chromium_launch_strips_app_mode(self):
        browser = window(1, 0, "chromium", 10)
        browser["cmdline"] = (
            "/usr/lib/chromium/chromium --app=https://example.com --flag"
        )
        self.assertEqual(
            "cd -- /tmp && /usr/lib/chromium/chromium --flag",
            self.module.launch_command(browser),
        )

    def test_nautilus_launch_strips_gapplication_service(self):
        nautilus = window(1, 0, "org.gnome.Nautilus", 10)
        nautilus["cmdline"] = "/usr/bin/nautilus --gapplication-service --new-window"

        self.assertEqual(
            "cd -- /tmp && /usr/bin/nautilus --new-window",
            self.module.launch_command(nautilus),
        )

    def test_webapp_url_requires_matching_chromium_app_class(self):
        row = window(
            1,
            0,
            "chromium",
            10,
            initial_title="example.com_/path",
        )
        self.assertIsNone(self.module.chromium_webapp_url(row))

    def test_webapp_url_requires_path_encoded_by_chromium_class(self):
        row = window(
            1,
            0,
            "chrome-example.com__safe-Default",
            10,
            initial_title="example.com_/other",
        )
        self.assertIsNone(self.module.chromium_webapp_url(row))

    def test_webapp_url_uses_validated_app_argument_when_title_is_not_url(self):
        row = window(
            1,
            0,
            "chrome-app.slack.com__client_team_channel-Default",
            10,
            initial_title="Slack",
        )
        row["cmdline"] = (
            "/usr/lib/chromium/chromium --app=https://app.slack.com/client/team/channel"
        )
        self.assertEqual(
            "https://app.slack.com/client/team/channel",
            self.module.chromium_webapp_url(row),
        )

    def test_webapp_profile_suffix_can_change_during_restore(self):
        row = window(
            1,
            0,
            "chrome-app.slack.com__client_team_channel-Default",
            10,
            initial_title="app.slack.com_/client/team/channel",
        )
        client = {
            "mapped": True,
            "address": "0x1",
            "class": "chrome-app.slack.com__client_team_channel-Profile_3",
            "initialClass": "chrome-app.slack.com__client_team_channel-Profile_3",
            "title": "Slack",
            "initialTitle": "app.slack.com_/client/team/channel",
            "workspace": {"id": 1},
        }
        self.assertEqual(1, self.module.client_matches(row, client))

    def test_shared_chromium_app_argument_does_not_duplicate_webapp(self):
        slack = window(
            1,
            0,
            "chrome-app.slack.com__client_team_channel-Default",
            10,
            initial_title="Slack",
        )
        browser = window(2, 1, "chromium", 10)
        shared_cmdline = (
            "/usr/lib/chromium/chromium --app=https://app.slack.com/client/team/channel"
        )
        slack["cmdline"] = shared_cmdline
        browser["cmdline"] = shared_cmdline

        self.assertEqual(
            [[slack], [browser]], self.module.process_groups([slack, browser])
        )
        self.assertEqual(
            "omarchy-launch-webapp https://app.slack.com/client/team/channel",
            self.module.launch_command(slack),
        )
        self.assertEqual(
            "cd -- /tmp && /usr/lib/chromium/chromium",
            self.module.launch_command(browser),
        )

    def test_unrecognized_webapp_cannot_supply_shared_browser_launch(self):
        unrecognized = window(
            1,
            0,
            "chrome-app.slack.com__client_team_channel-Default",
            10,
            initial_title="Slack",
        )
        browser = window(2, 1, "chromium", 10)
        shared_cmdline = (
            "/usr/lib/chromium/chromium --app=https://different.example.com/wrong"
        )
        unrecognized["cmdline"] = shared_cmdline
        browser["cmdline"] = shared_cmdline

        group = self.module.process_groups([unrecognized, browser])[0]
        self.assertIs(browser, self.module.process_launch_row(group))
        self.assertEqual(
            "cd -- /tmp && /usr/lib/chromium/chromium",
            self.module.launch_command(self.module.process_launch_row(group)),
        )

    def test_profile_alias_requires_plausible_webapp_class(self):
        self.assertFalse(
            self.module.window_classes_match(
                "chrome-arbitrary-Default",
                "chrome-arbitrary-Profile_3",
            )
        )

    def test_normal_chromium_class_alias_matches(self):
        row = window(1, 0, "chromium", 10, initial_title="New Tab - Chromium")
        client = {
            "mapped": True,
            "address": "0x1",
            "class": "chromium-browser",
            "initialClass": "chromium-browser",
            "title": "New Tab - Chromium",
            "initialTitle": "New Tab - Chromium",
            "workspace": {"id": 1},
        }
        self.assertEqual(1, self.module.client_matches(row, client))

    def test_match_prefers_current_title_when_initial_titles_are_blank(self):
        rows = [
            window(1, 0, "terminal", 10, title="Second"),
            window(2, 1, "terminal", 20, title="First"),
        ]
        clients = [
            {
                "mapped": True,
                "address": "0x1",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "First",
                "initialTitle": "",
                "workspace": {"id": 1},
            },
            {
                "mapped": True,
                "address": "0x2",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "Second",
                "initialTitle": "",
                "workspace": {"id": 1},
            },
        ]
        self.assertEqual({1: "0x2", 2: "0x1"}, self.module.match_windows(rows, clients))

    def test_discovery_never_claims_preexisting_unmatched_window(self):
        rows = [window(1, 0, "terminal", 10, title="Saved")]
        clients = [
            {
                "mapped": True,
                "address": "0xold",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "Unrelated",
                "workspace": {"id": 1},
            },
            {
                "mapped": True,
                "address": "0xnew",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "Saved",
                "workspace": {"id": 1},
            },
        ]
        matches = self.module.match_windows(rows, clients, {"0xold"})
        self.assertEqual({1: "0xnew"}, matches)

    def test_matching_does_not_claim_class_only_window_on_another_workspace(self):
        row = window(1, 0, "terminal", 10, title="Saved")
        client = {
            "mapped": True,
            "address": "0xother",
            "class": "terminal",
            "initialClass": "terminal",
            "title": "Other",
            "workspace": {"id": 2},
        }
        self.assertEqual({}, self.module.match_windows([row], [client]))

    def test_matching_does_not_claim_same_title_on_another_workspace(self):
        row = window(1, 0, "terminal", 10, title="Shared")
        client = {
            "mapped": True,
            "address": "0xother",
            "class": "terminal",
            "initialClass": "terminal",
            "title": "Shared",
            "workspace": {"id": 2},
        }
        self.assertEqual({}, self.module.match_windows([row], [client]))

    def test_matching_reassigns_flexible_row_to_preserve_unique_match(self):
        rows = [
            window(1, 0, "terminal", 10, title="Shared", initial_title="Flexible"),
            window(2, 1, "terminal", 20, title="Shared", initial_title="Unique"),
        ]
        clients = [
            {
                "mapped": True,
                "address": "0xshared",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "Shared",
                "initialTitle": "Unique",
                "workspace": {"id": 1},
            },
            {
                "mapped": True,
                "address": "0xflexible",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "Other",
                "initialTitle": "Flexible",
                "workspace": {"id": 1},
            },
        ]
        matches = self.module.match_windows(rows, clients, max_rank=1)
        self.assertEqual({1: "0xflexible", 2: "0xshared"}, matches)

    def test_matching_does_not_displace_exact_match_with_class_fallback(self):
        rows = [
            window(1, 0, "terminal", 10, title="Exact"),
            window(2, 1, "terminal", 20),
        ]
        clients = [
            {
                "mapped": True,
                "address": "0xexact",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "Exact",
                "workspace": {"id": 1},
            },
            {
                "mapped": True,
                "address": "0xfallback",
                "class": "terminal",
                "initialClass": "terminal",
                "title": "Other",
                "workspace": {"id": 1},
            },
        ]
        self.assertEqual(
            {1: "0xexact", 2: "0xfallback"},
            self.module.match_windows(rows, clients),
        )

    def test_initial_class_fallback_matches_on_saved_workspace(self):
        row = window(1, 0, "current-class", 10)
        row["initial_class"] = "stable-class"
        client = {
            "mapped": True,
            "address": "0x1",
            "class": "changed-class",
            "initialClass": "stable-class",
            "title": "",
            "workspace": {"id": 1},
        }
        self.assertEqual({1: "0x1"}, self.module.match_windows([row], [client]))

    def test_window_group_restore_preserves_saved_order(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        third = window(3, 2, "terminal", 30, title="Third")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        third.update(group_id=1, group_ord=2)
        before = [
            group_client(first, "0xa", []),
            group_client(second, "0xb", []),
            group_client(third, "0xc", []),
        ]
        after = [
            group_client(first, "0xa", ["0xa", "0xb", "0xc"]),
            group_client(second, "0xb", ["0xa", "0xb", "0xc"]),
            group_client(third, "0xc", ["0xa", "0xb", "0xc"]),
        ]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, before, after],
            ),
            mock.patch.object(self.module, "eval_lua", return_value=True) as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups(
                    [first, second, third], {1: "0xa", 2: "0xb", 3: "0xc"}
                )
            )

        lua = evaluate.call_args.args[0]
        self.assertIn("hl.dsp.group.toggle", lua)
        self.assertIn("g:add(w2, g.size + 1)", lua)
        self.assertIn("g:add(w3, g.size + 1)", lua)
        self.assertIn("hl.dsp.group.active({ index = 1", lua)
        self.assertIn("g.members[2] == w2", lua)
        self.assertIn("g.members[3] == w3", lua)

    def test_window_group_restore_is_gated_on_hyprland_056(self):
        first = window(1, 0, "terminal", 10)
        second = window(2, 1, "terminal", 20)
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        with (
            mock.patch.object(
                self.module, "hyprctl_json", return_value={"version": "0.55.0"}
            ) as hyprctl,
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )
        hyprctl.assert_called_once_with("version")
        evaluate.assert_not_called()

    def test_window_group_restore_skips_partial_group(self):
        first = window(1, 0, "terminal", 10)
        second = window(2, 1, "terminal", 20)
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        with (
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups([first, second], {1: "0xa"})
            )
        hyprctl.assert_not_called()
        evaluate.assert_not_called()

    def test_window_group_restore_skips_members_off_saved_workspace(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [group_client(first, "0xa", []), group_client(second, "0xb", [])]
        clients[1]["workspace"] = {"id": 2}
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients],
            ),
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log") as log,
        ):
            self.assertTrue(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )
        evaluate.assert_not_called()
        log.assert_any_call(
            "restore: skipping group whose members are not on their saved workspace"
        )

    def test_window_group_restore_skips_failed_monitor_placement(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [group_client(first, "0xa", []), group_client(second, "0xb", [])]
        with (
            mock.patch.object(
                self.module,
                "load_workspace_monitor_context",
                return_value=({}, {}, False),
            ),
            mock.patch.object(
                self.module, "prepare_workspace_monitor", return_value=False
            ),
            mock.patch.object(self.module, "place_window") as place,
            mock.patch.object(self.module, "load_focus_context", return_value={}),
            mock.patch.object(self.module, "restore_focus_context", return_value=True),
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log") as log,
        ):
            result = self.module.restore_windows(
                [first, second], {1: "0xa", 2: "0xb"}, clients
            )
        self.assertEqual((0, 2, 0, False), result)
        place.assert_not_called()
        hyprctl.assert_not_called()
        evaluate.assert_not_called()
        log.assert_any_call("restore: skipping group after failed monitor placement")

    def test_window_group_restore_skips_unrelated_existing_group(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [
            group_client(first, "0xa", ["0xa", "0xunrelated"]),
            group_client(second, "0xb", []),
        ]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients],
            ),
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )
        evaluate.assert_not_called()

    def test_window_group_restore_preserves_complete_existing_group(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [
            group_client(first, "0xa", ["0xa", "0xb"]),
            group_client(second, "0xb", ["0xa", "0xb"]),
        ]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients],
            ),
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )
        evaluate.assert_not_called()

    def test_window_group_restore_skips_fullscreen_group(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0, fullscreen=2)
        second.update(group_id=1, group_ord=1)
        with (
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )
        hyprctl.assert_not_called()
        evaluate.assert_not_called()

    def test_window_group_verification_failure_marks_restore_failed(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [group_client(first, "0xa", []), group_client(second, "0xb", [])]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients, clients],
            ),
            mock.patch.object(self.module, "eval_lua", return_value=True),
            mock.patch.object(self.module, "log"),
        ):
            self.assertFalse(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )

    def test_window_group_restore_skips_ambiguous_members(self):
        first = window(1, 0, "terminal", 10)
        second = window(2, 1, "terminal", 20)
        unrelated = window(3, 2, "terminal", 30)
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [
            group_client(first, "0xa", []),
            group_client(second, "0xb", []),
            group_client(unrelated, "0xc", []),
        ]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients],
            ),
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups(
                    [first, second, unrelated],
                    {1: "0xa", 2: "0xb", 3: "0xc"},
                )
            )
        evaluate.assert_not_called()

    def test_window_group_restore_skips_row_with_duplicate_live_matches(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [
            group_client(first, "0xa", []),
            group_client(second, "0xb", []),
            group_client(first, "0xc", []),
        ]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients],
            ),
            mock.patch.object(self.module, "eval_lua") as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )
        evaluate.assert_not_called()

    def test_window_group_restore_supports_singleton_group(self):
        row = window(1, 0, "terminal", 10, title="Only")
        row.update(group_id=1, group_ord=0)
        before = [group_client(row, "0xa", [])]
        after = [group_client(row, "0xa", ["0xa"])]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, before, after],
            ),
            mock.patch.object(self.module, "eval_lua", return_value=True) as evaluate,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(self.module.restore_window_groups([row], {1: "0xa"}))
        lua = evaluate.call_args.args[0]
        self.assertIn("hl.dsp.group.toggle", lua)
        self.assertNotIn("hl.dsp.group.active", lua)

    def test_window_group_eval_transport_failure_is_retryable(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [group_client(first, "0xa", []), group_client(second, "0xb", [])]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients],
            ),
            mock.patch.object(self.module, "eval_lua", return_value=None),
            mock.patch.object(self.module, "log"),
        ):
            self.assertIsNone(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )

    def test_window_group_eval_failure_marks_restore_failed(self):
        first = window(1, 0, "terminal", 10, title="First")
        second = window(2, 1, "terminal", 20, title="Second")
        first.update(group_id=1, group_ord=0)
        second.update(group_id=1, group_ord=1)
        clients = [group_client(first, "0xa", []), group_client(second, "0xb", [])]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[{"version": "0.56.2"}, clients],
            ),
            mock.patch.object(self.module, "eval_lua", return_value=False),
            mock.patch.object(self.module, "log"),
        ):
            self.assertFalse(
                self.module.restore_window_groups([first, second], {1: "0xa", 2: "0xb"})
            )

    def test_window_groups_restore_after_tiled_correction(self):
        first = window(1, 0, "first", 10)
        second = window(2, 1, "second", 20)
        first.update(at_x=0, at_y=0, size_w=500, size_h=1000, group_id=1, group_ord=0)
        second.update(at_x=0, at_y=0, size_w=500, size_h=1000, group_id=1, group_ord=1)
        events = []
        clients = [
            {"mapped": True, "address": "0xa"},
            {"mapped": True, "address": "0xb"},
        ]
        with (
            mock.patch.object(
                self.module,
                "load_workspace_monitor_context",
                return_value=({}, {}, False),
            ),
            mock.patch.object(
                self.module, "prepare_workspace_monitor", return_value=True
            ),
            mock.patch.object(self.module, "place_window", return_value=True),
            mock.patch.object(
                self.module,
                "restore_tiled_slots",
                side_effect=lambda *_args: events.append("tiled") or True,
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=clients),
            mock.patch.object(
                self.module,
                "restore_window_groups",
                side_effect=lambda *_args: events.append("groups") or True,
            ),
            mock.patch.object(
                self.module,
                "load_focus_context",
                return_value={"address": "0xa", "workspace_id": 1},
            ),
            mock.patch.object(self.module, "restore_focus_context", return_value=True),
        ):
            result = self.module.restore_windows(
                [first, second], {1: "0xa", 2: "0xb"}, clients
            )
        self.assertEqual((2, 0, 0, False), result)
        self.assertEqual(["tiled", "groups"], events)

    def test_focus_context_ipc_failure_does_not_block_tiled_or_group_restore(self):
        row = window(1, 0, "first", 10)
        row.update(at_x=0, at_y=0, size_w=500, size_h=1000)
        events = []
        clients = [{"mapped": True, "address": "0xa"}]
        with (
            mock.patch.object(
                self.module,
                "load_workspace_monitor_context",
                return_value=({}, {}, False),
            ),
            mock.patch.object(
                self.module, "prepare_workspace_monitor", return_value=True
            ),
            mock.patch.object(self.module, "place_window", return_value=True),
            mock.patch.object(
                self.module,
                "restore_tiled_slots",
                side_effect=lambda *_args: events.append("tiled") or True,
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=clients),
            mock.patch.object(
                self.module,
                "restore_window_groups",
                side_effect=lambda *_args: events.append("groups") or True,
            ),
            mock.patch.object(self.module, "load_focus_context", return_value=None),
            mock.patch.object(self.module, "restore_focus_context") as restore_focus,
            mock.patch.object(self.module, "log"),
        ):
            result = self.module.restore_windows([row], {1: "0xa"}, clients)
        self.assertEqual((1, 1, 0, False), result)
        self.assertEqual(["tiled", "groups"], events)
        restore_focus.assert_not_called()

    def run_restore(
        self,
        rows,
        wait_matches=None,
        dispatch_result=True,
        place_result=True,
        clients=None,
        appearances=None,
        appearance_workspaces=None,
        events=None,
        monitor_context=({}, {}, False),
        monitor_prepare_result=None,
        track_monitor=False,
        tiled_matches=None,
        tiled_result=True,
        group_result=True,
        sleep_intervals=None,
        log_messages=None,
    ):
        connection = mock.Mock()
        connection.close = mock.Mock()
        lock_file = mock.Mock()
        clients = clients or []
        wait_matches = wait_matches or {}
        appearances = appearances or {row_id: 1 for row_id in wait_matches}
        appearance_workspaces = appearance_workspaces or {}
        events = events if events is not None else []
        clock = [0.0]
        prepare_workspace_monitor = self.module.prepare_workspace_monitor

        def make_client(row, address):
            return {
                "mapped": True,
                "address": address,
                "class": row["class"],
                "initialClass": row["initial_class"],
                "title": row["title"],
                "initialTitle": row["initial_title"],
                "workspace": {
                    "id": appearance_workspaces.get(row["id"], row["workspace_id"])
                },
            }

        calls = [0]

        def current_clients(*_args):
            calls[0] += 1
            events.append("initial-scan" if calls[0] == 1 else "poll")
            visible = list(clients)
            for row in rows:
                threshold = appearances.get(row["id"])
                if threshold is not None and dispatch.call_count >= threshold:
                    visible.append(make_client(row, wait_matches[row["id"]]))
            return visible

        def run_dispatch(_lua, failure_context=None):
            if "exec_cmd" in _lua:
                self.assertEqual("restore: application launch", failure_context)
            events.append("dispatch")
            return dispatch_result

        def run_place(row, _address, _current=None):
            events.append(f"place:{row['id']}")
            return place_result

        def advance(seconds):
            if sleep_intervals is not None:
                sleep_intervals.append(seconds)
            clock[0] += seconds

        def run_monitor_prepare(*args):
            if track_monitor:
                events.append("monitor-prepare")
            if monitor_prepare_result is not None:
                return monitor_prepare_result
            return prepare_workspace_monitor(*args)

        def run_tiled_restore(_rows, _matches, _clients, *_args):
            events.append("tiled-restore")
            if tiled_matches is not None:
                tiled_matches.append(dict(_matches))
            return tiled_result

        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "test-instance"},
                clear=False,
            ),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "db_conn", return_value=connection),
            mock.patch.object(
                self.module, "latest_session", return_value=(1, "periodic", "now")
            ),
            mock.patch.object(self.module, "load_windows", return_value=rows),
            mock.patch.object(self.module, "load_workspace_layouts", return_value={}),
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=current_clients,
            ),
            # This harness models only the `clients` query, so animation
            # suppression is exercised by its own focused tests instead.
            mock.patch.object(self.module, "animations_enabled", return_value=None),
            mock.patch.object(self.module, "set_animations_enabled", return_value=True),
            mock.patch.object(
                self.module, "dispatch", side_effect=run_dispatch
            ) as dispatch,
            mock.patch.object(
                self.module.time, "monotonic", side_effect=lambda: clock[0]
            ),
            mock.patch.object(self.module.time, "sleep", side_effect=advance),
            mock.patch.object(
                self.module,
                "log",
                side_effect=log_messages.append if log_messages is not None else None,
            ),
            mock.patch.object(
                self.module, "place_window", side_effect=run_place
            ) as place,
            mock.patch.object(
                self.module,
                "load_workspace_monitor_context",
                return_value=monitor_context,
            ),
            mock.patch.object(
                self.module,
                "prepare_workspace_monitor",
                side_effect=run_monitor_prepare,
            ),
            mock.patch.object(
                self.module, "restore_tiled_slots", side_effect=run_tiled_restore
            ),
            mock.patch.object(
                self.module, "restore_window_groups", return_value=group_result
            ),
            mock.patch.object(
                self.module,
                "load_focus_context",
                return_value={"address": "0xfocused", "workspace_id": 1},
            ),
            mock.patch.object(self.module, "restore_focus_context", return_value=True),
        ):
            result = self.module.cmd_restore()
        return result, dispatch, place

    def test_browser_process_launches_once_and_places_two_windows(self):
        rows = [window(1, 0, "chromium", 10), window(2, 1, "slack-webapp", 10)]
        result, dispatch, place = self.run_restore(rows, {1: "0x1", 2: "0x2"})
        self.assertEqual(0, result)
        self.assertEqual(1, dispatch.call_count)
        self.assertEqual(2, place.call_count)

    def test_process_group_relaunches_for_a_still_missing_window(self):
        rows = [window(1, 0, "files", 10), window(2, 1, "files", 10)]
        sleep_intervals = []
        result, dispatch, place = self.run_restore(
            rows,
            {1: "0x1", 2: "0x2"},
            appearances={1: 1, 2: 2},
            sleep_intervals=sleep_intervals,
        )
        self.assertEqual(0, result)
        self.assertEqual(2, dispatch.call_count)
        self.assertEqual(2, place.call_count)
        self.assertTrue(sleep_intervals)
        self.assertEqual({self.module.RESTORE_POLL_INTERVAL}, set(sleep_intervals))
        self.assertEqual(0.05, self.module.RESTORE_POLL_INTERVAL)

    def test_existing_workspace_match_avoids_duplicate_launch(self):
        row = window(1, 0, "terminal", 10, title="Saved")
        client = {
            "mapped": True,
            "address": "0x1",
            "class": "terminal",
            "initialClass": "terminal",
            "title": "Changed",
            "workspace": {"id": 1},
        }
        result, dispatch, place = self.run_restore([row], clients=[client])
        self.assertEqual(0, result)
        dispatch.assert_not_called()
        place.assert_called_once()

    def test_same_class_different_processes_launch_twice(self):
        rows = [window(1, 0, "terminal", 10), window(2, 1, "terminal", 20)]
        result, dispatch, place = self.run_restore(rows, {1: "0x1", 2: "0x2"})
        self.assertEqual(0, result)
        self.assertEqual(2, dispatch.call_count)
        self.assertEqual(2, place.call_count)

    def test_all_missing_groups_launch_before_first_poll(self):
        browser = window(1, 0, "chromium", 10)
        webapp = window(
            2,
            1,
            "chrome-discord.com__channels_@me-Default",
            10,
            initial_title="discord.com_/channels/@me",
        )
        events = []

        result, dispatch, place = self.run_restore(
            [browser, webapp],
            wait_matches={1: "0x1", 2: "0x2"},
            events=events,
        )

        self.assertEqual(0, result)
        self.assertEqual(2, dispatch.call_count)
        self.assertEqual(2, place.call_count)
        self.assertEqual(["initial-scan", "dispatch", "dispatch", "poll"], events[:4])

    def test_tiled_workspace_is_corrected_before_unrelated_window_times_out(self):
        first = window(1, 0, "left", 10)
        second = window(2, 1, "right", 20)
        slow = window(3, 2, "slow", 30)
        first.update(at_x=0, at_y=0, size_w=500, size_h=1000)
        second.update(at_x=500, at_y=0, size_w=500, size_h=1000)
        slow["workspace_id"] = 2
        events = []

        with mock.patch.object(
            self.module,
            "restore_tiled_layouts",
            side_effect=lambda *_args: events.append("tiled") or True,
        ):
            result, _dispatch, _place = self.run_restore(
                [first, second, slow],
                wait_matches={2: "0x2", 3: "0x3"},
                appearances={2: 2, 3: 4},
                clients=[
                    {
                        "mapped": True,
                        "address": "0x1",
                        "class": first["class"],
                        "initialClass": first["initial_class"],
                        "title": first["title"],
                        "initialTitle": first["initial_title"],
                        "workspace": {"id": 1},
                    }
                ],
                events=events,
            )

        self.assertEqual(1, result)
        self.assertIn("tiled", events)
        polls = [index for index, event in enumerate(events) if event == "poll"]
        self.assertGreaterEqual(len(polls), 2)
        self.assertLess(events.index("tiled"), polls[1])

    def test_launched_window_is_discovered_before_workspace_placement(self):
        row = window(1, 0, "terminal", 10, title="Saved")
        result, dispatch, place = self.run_restore(
            [row],
            wait_matches={1: "0x1"},
            appearance_workspaces={1: 2},
        )
        self.assertEqual(0, result)
        self.assertEqual(1, dispatch.call_count)
        place.assert_called_once()

    def nested_workspace(self):
        left = window(1, 0, "left", 10)
        top_right = window(2, 1, "top-right", 20)
        bottom_right = window(3, 2, "bottom-right", 30)
        left.update(at_x=0, at_y=0, size_w=300, size_h=1000)
        top_right.update(at_x=300, at_y=0, size_w=700, size_h=500)
        bottom_right.update(at_x=300, at_y=500, size_w=700, size_h=500)
        return [left, top_right, bottom_right]

    def test_split_tree_infers_nested_three_window_layout(self):
        rows = self.nested_workspace()
        tree = self.module.infer_split_tree(
            [(row["id"], self.module.saved_geometry(row)) for row in rows]
        )
        self.assertEqual("x", tree[1])
        self.assertEqual(("leaf", 1), tree[3])
        self.assertEqual("y", tree[4][1])
        self.assertEqual(
            [1, 2, 3],
            [self.module.split_tree_seed(tree), tree[4][3][1], tree[4][4][1]],
        )

    def test_split_tree_uses_workarea_to_account_for_gaps(self):
        items = [
            (1, (12, 38, 849, 1300)),
            (2, (875, 38, 1513, 643)),
            (3, (875, 695, 1513, 643)),
        ]
        tree = self.module.infer_split_tree(items, (10, 36, 2380, 1304), (5, 5, 5, 5))
        self.assertAlmostEqual((868 - 10) / 2380, tree[2])
        self.assertAlmostEqual(0.5, tree[4][2])

    def test_split_tree_rejects_ambiguous_grid_and_overlaps(self):
        grid = [
            (1, (0, 0, 500, 500)),
            (2, (500, 0, 500, 500)),
            (3, (0, 500, 500, 500)),
            (4, (500, 500, 500, 500)),
        ]
        overlapping = [(1, (0, 0, 600, 1000)), (2, (500, 0, 500, 1000))]
        self.assertIsNone(self.module.infer_split_tree(grid))
        self.assertIsNone(self.module.infer_split_tree(overlapping))
        self.assertIsNone(
            self.module.infer_split_tree(
                [(index, (index * 250, 0, 250, 1000)) for index in range(4)]
            )
        )

    def test_replay_lua_stages_nonseed_and_rebuilds_tree_in_order(self):
        rows = self.nested_workspace()
        matches = {1: "0xleft", 2: "0xtop", 3: "0xbottom"}
        tree = self.module.infer_split_tree(
            [(row["id"], self.module.saved_geometry(row)) for row in rows]
        )
        with mock.patch.object(self.module, "eval_lua", return_value=True) as evaluate:
            result, addresses = self.module.replay_workspace_tree(
                1,
                rows,
                matches,
                tree,
                {"address": "0xfocused", "workspace_id": 2},
            )

        self.assertTrue(result)
        self.assertEqual(["0xleft", "0xtop", "0xbottom"], addresses)
        lua = evaluate.call_args.args[0]
        self.assertIn("local function run(result)", lua)
        self.assertIn("local ok,err=pcall", lua)
        self.assertEqual(2, lua.count("name:omarchy-sesh-stage"))
        self.assertIn("preselect right", lua)
        self.assertIn("preselect down", lua)
        self.assertIn("splitratio 0.6 exact", lua)
        self.assertIn("splitratio 1 exact", lua)
        self.assertIn("window = [[address:0xfocused]]", lua)

    def test_nested_replay_restores_observed_layout_and_verifies(self):
        rows = self.nested_workspace()
        matches = {1: "0xleft", 2: "0xtop", 3: "0xbottom"}
        current = [
            tiled_client(rows[0], "0xleft", [0, 0], [500, 1000]),
            tiled_client(rows[1], "0xtop", [500, 0], [500, 500]),
            tiled_client(rows[2], "0xbottom", [500, 500], [500, 500]),
        ]
        restored = [
            tiled_client(rows[0], "0xleft", [0, 0], [300, 1000]),
            tiled_client(rows[1], "0xtop", [300, 0], [700, 500]),
            tiled_client(rows[2], "0xbottom", [300, 500], [700, 500]),
        ]
        workspaces, monitors = live_workspace_context()
        focus = {"address": "0xfocused", "workspace_id": 2}
        with (
            mock.patch.object(
                self.module, "nested_replay_supported", return_value=True
            ),
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=layout_ipc_response(workspaces, monitors, restored),
            ),
            mock.patch.object(
                self.module,
                "replay_workspace_tree",
                return_value=(True, list(matches.values())),
            ) as replay,
        ):
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows, matches, current, {1: workspace_layout()}, focus
                )
            )
        replay.assert_called_once()

    def test_two_window_replay_restores_gap_aware_ratio_and_verifies(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=12, at_y=12, size_w=287, size_h=976)
        right.update(at_x=309, at_y=12, size_w=679, size_h=976)
        rows = [left, right]
        matches = {1: "0xleft", 2: "0xright"}
        current = [
            tiled_client(left, "0xleft", [12, 12], [483, 976]),
            tiled_client(right, "0xright", [505, 12], [483, 976]),
        ]
        restored = [
            tiled_client(left, "0xleft", [12, 12], [287, 976]),
            tiled_client(right, "0xright", [309, 12], [679, 976]),
        ]
        layout = workspace_layout(width=976, height=976)
        layout.update(
            at_x=12,
            at_y=12,
            work_x=10,
            work_y=10,
            work_w=980,
            work_h=980,
            gap_top=5,
            gap_right=5,
            gap_bottom=5,
            gap_left=5,
        )
        workspaces, monitors = live_workspace_context()

        def response(endpoint, *args):
            if endpoint == "workspacerules":
                return []
            if endpoint == "workspaces":
                return workspaces
            if endpoint == "monitors":
                return monitors
            if endpoint == "getoption":
                return {"css": "10" if args == ("general:gaps_out",) else "5"}
            if endpoint == "clients":
                return restored
            raise AssertionError(f"unexpected endpoint: {endpoint}")

        with (
            mock.patch.object(
                self.module, "nested_replay_supported", return_value=True
            ),
            mock.patch.object(self.module, "hyprctl_json", side_effect=response),
            mock.patch.object(
                self.module,
                "load_focus_context",
                return_value={"address": "0xfocused", "workspace_id": 2},
            ),
            mock.patch.object(
                self.module, "restore_focus_context", return_value=True
            ) as restore_focus,
            mock.patch.object(
                self.module,
                "replay_workspace_tree",
                return_value=(True, list(matches.values())),
            ) as replay,
        ):
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    current,
                    {1: layout},
                )
            )

        tree = replay.call_args.args[3]
        self.assertEqual("x", tree[1])
        self.assertAlmostEqual(0.3, tree[2])
        restore_focus.assert_called_once_with(
            {"address": "0xfocused", "workspace_id": 2}
        )

    def test_two_window_vertical_replay_uses_exact_saved_ratio(self):
        top = window(1, 0, "top", 10)
        bottom = window(2, 1, "bottom", 20)
        top.update(at_x=0, at_y=0, size_w=1000, size_h=700)
        bottom.update(at_x=0, at_y=700, size_w=1000, size_h=300)
        rows = [top, bottom]
        matches = {1: "0xtop", 2: "0xbottom"}
        tree = self.module.infer_split_tree(
            [(row["id"], self.module.saved_geometry(row)) for row in rows]
        )

        with mock.patch.object(self.module, "eval_lua", return_value=True) as evaluate:
            self.module.replay_workspace_tree(
                1,
                rows,
                matches,
                tree,
                {"address": "0xfocused", "workspace_id": 2},
            )

        lua = evaluate.call_args.args[0]
        self.assertIn("preselect down", lua)
        self.assertIn("splitratio 1.4 exact", lua)

    def test_nested_replay_skips_incomplete_or_incompatible_workspace(self):
        rows = self.nested_workspace()
        matches = {1: "0xleft", 2: "0xtop", 3: "0xbottom"}
        clients = [
            tiled_client(rows[0], "0xleft", [0, 0], [500, 1000]),
            tiled_client(rows[1], "0xtop", [500, 0], [500, 500]),
            tiled_client(rows[2], "0xbottom", [500, 500], [500, 500]),
        ]
        unrelated = tiled_client(
            window(4, 3, "other", 40), "0xother", [0, 0], [100, 100]
        )
        unrelated["workspace"] = {"id": 1}
        workspaces, monitors = live_workspace_context()
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=layout_ipc_response(workspaces, monitors),
            ),
            mock.patch.object(self.module, "replay_workspace_tree") as replay,
        ):
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    clients,
                    {1: workspace_layout(complete=0)},
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    clients,
                    {1: workspace_layout(width=1200)},
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    clients + [unrelated],
                    {1: workspace_layout()},
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )
        replay.assert_not_called()

    def test_nested_replay_skips_live_master_layout_and_current_group(self):
        rows = self.nested_workspace()
        matches = {1: "0xleft", 2: "0xtop", 3: "0xbottom"}
        clients = [
            tiled_client(rows[0], "0xleft", [0, 0], [500, 1000]),
            tiled_client(rows[1], "0xtop", [500, 0], [500, 500]),
            tiled_client(rows[2], "0xbottom", [500, 500], [500, 500]),
        ]
        workspaces, monitors = live_workspace_context()
        workspaces[0]["tiledLayout"] = "master"
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=layout_ipc_response(workspaces, monitors),
            ),
            mock.patch.object(self.module, "replay_workspace_tree") as replay,
        ):
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    clients,
                    {1: workspace_layout()},
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )
        replay.assert_not_called()

        workspaces[0]["tiledLayout"] = "dwindle"
        clients[0]["grouped"] = ["0xleft", "0xtop"]
        with (
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=layout_ipc_response(workspaces, monitors),
            ),
            mock.patch.object(self.module, "replay_workspace_tree") as replay,
        ):
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    clients,
                    {1: workspace_layout()},
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )
        replay.assert_not_called()

    def test_nested_replay_checks_workspace_rules_before_context_queries(self):
        rows = self.nested_workspace()
        matches = {1: "0xleft", 2: "0xtop", 3: "0xbottom"}
        clients = [
            tiled_client(rows[0], "0xleft", [0, 0], [500, 1000]),
            tiled_client(rows[1], "0xtop", [500, 0], [500, 500]),
            tiled_client(rows[2], "0xbottom", [500, 500], [500, 500]),
        ]
        with (
            mock.patch.object(
                self.module, "hyprctl_json", return_value=[{"workspaceString": "1"}]
            ) as hyprctl,
            mock.patch.object(self.module, "replay_workspace_tree") as replay,
            mock.patch.object(self.module, "log"),
        ):
            self.assertTrue(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    clients,
                    {1: workspace_layout()},
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )
        hyprctl.assert_called_once_with("workspacerules")
        replay.assert_not_called()

    def test_nested_replay_failure_recovers_staged_windows(self):
        rows = self.nested_workspace()
        matches = {1: "0xleft", 2: "0xtop", 3: "0xbottom"}
        clients = [
            tiled_client(rows[0], "0xleft", [0, 0], [500, 1000]),
            tiled_client(rows[1], "0xtop", [500, 0], [500, 500]),
            tiled_client(rows[2], "0xbottom", [500, 500], [500, 500]),
        ]
        workspaces, monitors = live_workspace_context()
        focus = {"address": "0xfocused", "workspace_id": 2}
        with (
            mock.patch.object(
                self.module, "nested_replay_supported", return_value=True
            ),
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=layout_ipc_response(workspaces, monitors, clients),
            ),
            mock.patch.object(
                self.module,
                "replay_workspace_tree",
                return_value=(False, list(matches.values())),
            ),
            mock.patch.object(
                self.module, "recover_replay_workspace", return_value=True
            ) as recover,
            mock.patch.object(self.module, "log"),
        ):
            self.assertFalse(
                self.module.restore_nested_tiled_layouts(
                    rows, matches, clients, {1: workspace_layout()}, focus
                )
            )
        recover.assert_called_once_with(1, ["0xleft", "0xtop", "0xbottom"], focus)

    def test_tiled_layout_helper_runs_slot_fallback_after_nested_failure(self):
        rows = self.nested_workspace()
        clients = [tiled_client(rows[0], "0xleft", [0, 0], [500, 1000])]
        with (
            mock.patch.object(
                self.module, "restore_nested_tiled_layouts", return_value=False
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=clients),
            mock.patch.object(
                self.module, "restore_tiled_slots", return_value=True
            ) as slots,
        ):
            self.assertFalse(
                self.module.restore_tiled_layouts(rows, {1: "0xleft"}, clients, {})
            )
        slots.assert_called_once_with(rows, {1: "0xleft"}, clients)

    def test_nested_replay_ipc_failure_is_retryable(self):
        rows = self.nested_workspace()
        matches = {1: "0xleft", 2: "0xtop", 3: "0xbottom"}
        clients = [
            tiled_client(rows[0], "0xleft", [0, 0], [500, 1000]),
            tiled_client(rows[1], "0xtop", [500, 0], [500, 500]),
            tiled_client(rows[2], "0xbottom", [500, 500], [500, 500]),
        ]
        workspaces, monitors = live_workspace_context()
        with (
            mock.patch.object(
                self.module, "nested_replay_supported", return_value=None
            ),
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=layout_ipc_response(workspaces, monitors),
            ),
            mock.patch.object(self.module, "replay_workspace_tree") as replay,
        ):
            self.assertIsNone(
                self.module.restore_nested_tiled_layouts(
                    rows,
                    matches,
                    clients,
                    {1: workspace_layout()},
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )
        replay.assert_not_called()

    def test_replay_recovery_preserves_mid_dispatch_ipc_failure(self):
        clients = [
            {
                "mapped": True,
                "address": "0xa",
                "workspace": {"id": 99},
            }
        ]
        with (
            mock.patch.object(self.module, "hyprctl_json", side_effect=[clients, None]),
            mock.patch.object(self.module, "dispatch", return_value=False),
        ):
            self.assertIsNone(
                self.module.recover_replay_workspace(
                    1,
                    ["0xa"],
                    {"address": "0xfocused", "workspace_id": 2},
                )
            )

    def test_focus_restore_returns_to_empty_workspace(self):
        with (
            mock.patch.object(self.module, "dispatch", return_value=True) as dispatch,
            mock.patch.object(self.module, "hyprctl_json", return_value={"id": 5}),
        ):
            self.assertTrue(
                self.module.restore_focus_context({"address": "", "workspace_id": 5})
            )
        dispatch.assert_called_once_with("hl.dsp.focus({ workspace = 5 })")

    def test_focus_restore_reopens_special_workspace_on_saved_monitor(self):
        before = [
            {
                "name": "DP-1",
                "focused": False,
                "specialWorkspace": {"id": 0, "name": ""},
            },
            {
                "name": "DP-2",
                "focused": True,
                "specialWorkspace": {"id": 9, "name": "special:scratch"},
            },
        ]
        focused = [
            {
                "name": "DP-1",
                "focused": True,
                "specialWorkspace": {"id": 0, "name": ""},
            }
        ]
        restored = [
            {
                "name": "DP-1",
                "focused": True,
                "specialWorkspace": {"id": 9, "name": "special:scratch"},
            }
        ]
        with (
            mock.patch.object(self.module, "dispatch", return_value=True) as dispatch,
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=[before, focused, restored],
            ),
        ):
            self.assertTrue(
                self.module.restore_focus_context(
                    {
                        "address": "",
                        "workspace_id": 1,
                        "monitor_name": "DP-1",
                        "special_workspace": "special:scratch",
                    }
                )
            )
        self.assertEqual(
            [
                "hl.dsp.focus({ monitor = [[DP-1]] })",
                "hl.dsp.workspace.toggle_special([[scratch]])",
            ],
            [call.args[0] for call in dispatch.call_args_list],
        )

    def test_nested_replay_capability_checks_version_and_dwindle_options(self):
        enabled = [
            {"bool": True},
            {"bool": True},
            {"bool": False},
        ]
        with (
            mock.patch.object(
                self.module, "hyprctl_json", return_value={"version": "0.56.2"}
            ) as hyprctl,
            mock.patch.object(
                self.module, "hyprctl_batch_json", return_value=enabled
            ) as batch,
        ):
            self.assertTrue(self.module.nested_replay_supported())
        hyprctl.assert_called_once_with("version")
        batch.assert_called_once_with(
            "getoption dwindle:use_active_for_splits",
            "getoption dwindle:preserve_split",
            "getoption dwindle:permanent_direction_override",
        )

        permanent = [
            {"bool": True},
            {"bool": True},
            {"bool": True},
        ]
        with (
            mock.patch.object(
                self.module, "hyprctl_json", return_value={"version": "0.56.2"}
            ),
            mock.patch.object(
                self.module, "hyprctl_batch_json", return_value=permanent
            ),
            mock.patch.object(self.module, "log"),
        ):
            self.assertFalse(self.module.nested_replay_supported())

    def test_tiled_slot_restore_corrects_chromium_appearance_order(self):
        slack = window(1, 0, "slack", 10)
        discord = window(2, 1, "discord", 20)
        chrome = window(3, 2, "chromium", 30)
        slack.update(at_x=0, at_y=0, size_w=500, size_h=500)
        discord.update(at_x=0, at_y=500, size_w=500, size_h=500)
        chrome.update(at_x=500, at_y=0, size_w=1000, size_h=1000)
        matches = {1: "0xslack", 2: "0xdiscord", 3: "0xchrome"}
        clients = [
            {
                "mapped": True,
                "address": "0xdiscord",
                "class": "discord",
                "initialClass": "discord",
                "workspace": {"id": 1},
                "at": [0, 0],
                "size": [500, 500],
                "floating": False,
                "fullscreen": 0,
            },
            {
                "mapped": True,
                "address": "0xchrome",
                "class": "chromium",
                "initialClass": "chromium",
                "workspace": {"id": 1},
                "at": [0, 500],
                "size": [500, 500],
                "floating": False,
                "fullscreen": 0,
            },
            {
                "mapped": True,
                "address": "0xslack",
                "class": "slack",
                "initialClass": "slack",
                "workspace": {"id": 1},
                "at": [500, 0],
                "size": [1000, 1000],
                "floating": False,
                "fullscreen": 0,
            },
        ]

        with mock.patch.object(self.module, "dispatch", return_value=True) as dispatch:
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [slack, discord, chrome], matches, clients
                )
            )

        self.assertEqual(
            [
                "hl.dsp.window.swap({ window = [[address:0xslack]], target = [[address:0xdiscord]] })",
                "hl.dsp.window.swap({ window = [[address:0xdiscord]], target = [[address:0xchrome]] })",
            ],
            [call.args[0] for call in dispatch.call_args_list],
        )

    def test_tiled_slot_restore_skips_incompatible_geometry(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=500, size_h=1000)
        right.update(at_x=500, at_y=0, size_w=500, size_h=1000)
        clients = [
            {
                "mapped": True,
                "address": "0xleft",
                "workspace": {"id": 1},
                "at": [0, 0],
                "size": [400, 1000],
                "floating": False,
                "fullscreen": 0,
            },
            {
                "mapped": True,
                "address": "0xright",
                "workspace": {"id": 1},
                "at": [400, 0],
                "size": [600, 1000],
                "floating": False,
                "fullscreen": 0,
            },
        ]

        with mock.patch.object(self.module, "dispatch") as dispatch:
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [left, right], {1: "0xleft", 2: "0xright"}, clients
                )
            )
        dispatch.assert_not_called()

    def test_tiled_slot_restore_resizes_saved_ratio_then_refreshes(self):
        chrome = window(1, 0, "chromium", 10)
        terminal = window(2, 1, "terminal", 20)
        chrome.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        terminal.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        matches = {1: "0xchrome", 2: "0xterminal"}
        clients = [
            tiled_client(chrome, "0xchrome", [0, 0], [500, 1000]),
            tiled_client(terminal, "0xterminal", [500, 0], [500, 1000]),
        ]
        refreshed = [
            tiled_client(chrome, "0xchrome", [0, 0], [700, 1000]),
            tiled_client(terminal, "0xterminal", [700, 0], [300, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=True) as dispatch,
            mock.patch.object(
                self.module, "hyprctl_json", return_value=refreshed
            ) as hyprctl,
        ):
            self.assertTrue(
                self.module.restore_tiled_slots([chrome, terminal], matches, clients)
            )

        self.assertEqual(
            [
                "hl.dsp.window.resize({ x = 700, y = 1000, window = [[address:0xchrome]] })",
            ],
            [call.args[0] for call in dispatch.call_args_list],
        )
        hyprctl.assert_called_once_with("clients")

    def test_tiled_slot_restore_rejects_ratio_that_does_not_settle(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=300, size_h=1000)
        right.update(at_x=300, at_y=0, size_w=700, size_h=1000)
        matches = {1: "0xleft", 2: "0xright"}
        clients = [
            tiled_client(left, "0xleft", [0, 0], [500, 1000]),
            tiled_client(right, "0xright", [500, 0], [500, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=True),
            mock.patch.object(self.module, "hyprctl_json", return_value=clients),
            mock.patch.object(self.module, "log") as log,
        ):
            self.assertFalse(
                self.module.restore_tiled_slots([left, right], matches, clients)
            )
        log.assert_any_call("restore: tiled geometry did not settle on workspace 1")

    def test_tiled_slot_restore_resizes_after_workspace_origin_changes(self):
        chrome = window(1, 0, "chromium", 10)
        terminal = window(2, 1, "terminal", 20)
        chrome.update(at_x=1920, at_y=0, size_w=700, size_h=1000)
        terminal.update(at_x=2620, at_y=0, size_w=300, size_h=1000)
        matches = {1: "0xchrome", 2: "0xterminal"}
        clients = [
            tiled_client(chrome, "0xchrome", [0, 0], [500, 1000]),
            tiled_client(terminal, "0xterminal", [500, 0], [500, 1000]),
        ]
        refreshed = [
            tiled_client(chrome, "0xchrome", [0, 0], [700, 1000]),
            tiled_client(terminal, "0xterminal", [700, 0], [300, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=True) as dispatch,
            mock.patch.object(
                self.module, "hyprctl_json", return_value=refreshed
            ) as hyprctl,
        ):
            self.assertTrue(
                self.module.restore_tiled_slots([chrome, terminal], matches, clients)
            )

        dispatch.assert_called_once_with(
            "hl.dsp.window.resize({ x = 700, y = 1000, window = [[address:0xchrome]] })"
        )
        hyprctl.assert_called_once_with("clients")

    def test_tiled_slot_restore_swaps_reversed_ratio_before_resizing(self):
        chrome = window(1, 0, "chromium", 10)
        terminal = window(2, 1, "terminal", 20)
        chrome.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        terminal.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        matches = {1: "0xchrome", 2: "0xterminal"}
        clients = [
            tiled_client(terminal, "0xterminal", [0, 0], [500, 1000]),
            tiled_client(chrome, "0xchrome", [500, 0], [500, 1000]),
        ]
        arranged = [
            tiled_client(chrome, "0xchrome", [0, 0], [500, 1000]),
            tiled_client(terminal, "0xterminal", [500, 0], [500, 1000]),
        ]
        refreshed = [
            tiled_client(chrome, "0xchrome", [0, 0], [700, 1000]),
            tiled_client(terminal, "0xterminal", [700, 0], [300, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=True) as dispatch,
            mock.patch.object(
                self.module, "hyprctl_json", side_effect=[arranged, refreshed]
            ) as hyprctl,
        ):
            self.assertTrue(
                self.module.restore_tiled_slots([chrome, terminal], matches, clients)
            )

        self.assertEqual(
            [
                "hl.dsp.window.swap({ window = [[address:0xchrome]], target = [[address:0xterminal]] })",
                "hl.dsp.window.resize({ x = 700, y = 1000, window = [[address:0xchrome]] })",
            ],
            [call.args[0] for call in dispatch.call_args_list],
        )
        self.assertEqual(
            [mock.call("clients"), mock.call("clients")], hyprctl.call_args_list
        )

    def test_tiled_slot_restore_does_not_resize_when_swap_has_no_effect(self):
        chrome = window(1, 0, "chromium", 10)
        terminal = window(2, 1, "terminal", 20)
        chrome.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        terminal.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        matches = {1: "0xchrome", 2: "0xterminal"}
        reversed_clients = [
            tiled_client(terminal, "0xterminal", [0, 0], [500, 1000]),
            tiled_client(chrome, "0xchrome", [500, 0], [500, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=True) as dispatch,
            mock.patch.object(
                self.module, "hyprctl_json", return_value=reversed_clients
            ) as hyprctl,
        ):
            self.assertFalse(
                self.module.restore_tiled_slots(
                    [chrome, terminal], matches, reversed_clients
                )
            )
        self.assertEqual(1, dispatch.call_count)
        hyprctl.assert_called_once_with("clients")

    def test_tiled_slot_restore_resizes_vertical_ratio(self):
        top = window(1, 0, "top", 10)
        bottom = window(2, 1, "bottom", 20)
        top.update(at_x=0, at_y=0, size_w=1000, size_h=700)
        bottom.update(at_x=0, at_y=700, size_w=1000, size_h=300)
        clients = [
            tiled_client(top, "0xtop", [0, 0], [1000, 500]),
            tiled_client(bottom, "0xbottom", [0, 500], [1000, 500]),
        ]
        refreshed = [
            tiled_client(top, "0xtop", [0, 0], [1000, 700]),
            tiled_client(bottom, "0xbottom", [0, 700], [1000, 300]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=True) as dispatch,
            mock.patch.object(self.module, "hyprctl_json", return_value=refreshed),
        ):
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [top, bottom], {1: "0xtop", 2: "0xbottom"}, clients
                )
            )
        dispatch.assert_called_once_with(
            "hl.dsp.window.resize({ x = 1000, y = 700, window = [[address:0xtop]] })"
        )

    def test_tiled_slot_restore_does_not_resize_different_workspace_dimensions(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        right.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        clients = [
            tiled_client(left, "0xleft", [0, 0], [600, 900]),
            tiled_client(right, "0xright", [600, 0], [600, 900]),
        ]

        with (
            mock.patch.object(self.module, "dispatch") as dispatch,
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
        ):
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [left, right], {1: "0xleft", 2: "0xright"}, clients
                )
            )
        dispatch.assert_not_called()
        hyprctl.assert_not_called()

    def test_tiled_slot_restore_does_not_resize_different_split_axis(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        right.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        clients = [
            tiled_client(left, "0xleft", [0, 0], [1000, 500]),
            tiled_client(right, "0xright", [0, 500], [1000, 500]),
        ]

        with (
            mock.patch.object(self.module, "dispatch") as dispatch,
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
        ):
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [left, right], {1: "0xleft", 2: "0xright"}, clients
                )
            )
        dispatch.assert_not_called()
        hyprctl.assert_not_called()

    def test_tiled_slot_restore_does_not_resize_incomplete_workspace(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        right.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        clients = [tiled_client(left, "0xleft", [0, 0], [1000, 1000])]

        with (
            mock.patch.object(self.module, "dispatch") as dispatch,
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
        ):
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [left, right], {1: "0xleft", 2: "0xright"}, clients
                )
            )
        dispatch.assert_not_called()
        hyprctl.assert_not_called()

    def test_tiled_slot_restore_does_not_resize_nested_layout(self):
        top_left = window(1, 0, "top-left", 10)
        bottom_left = window(2, 1, "bottom-left", 20)
        right = window(3, 2, "right", 30)
        top_left.update(at_x=0, at_y=0, size_w=300, size_h=500)
        bottom_left.update(at_x=0, at_y=500, size_w=300, size_h=500)
        right.update(at_x=300, at_y=0, size_w=700, size_h=1000)
        rows = [top_left, bottom_left, right]
        matches = {1: "0xtop", 2: "0xbottom", 3: "0xright"}
        clients = [
            tiled_client(top_left, "0xtop", [0, 0], [500, 500]),
            tiled_client(bottom_left, "0xbottom", [0, 500], [500, 500]),
            tiled_client(right, "0xright", [500, 0], [500, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch") as dispatch,
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
        ):
            self.assertTrue(self.module.restore_tiled_slots(rows, matches, clients))
        dispatch.assert_not_called()
        hyprctl.assert_not_called()

    def test_tiled_slot_resize_dispatch_failure_marks_restore_failed(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        right.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        matches = {1: "0xleft", 2: "0xright"}
        clients = [
            tiled_client(left, "0xleft", [0, 0], [500, 1000]),
            tiled_client(right, "0xright", [500, 0], [500, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=False) as dispatch,
            mock.patch.object(
                self.module, "hyprctl_json", return_value=clients
            ) as hyprctl,
        ):
            self.assertFalse(
                self.module.restore_tiled_slots([left, right], matches, clients)
            )
        self.assertEqual(1, dispatch.call_count)
        hyprctl.assert_called_once_with("clients")

    def test_tiled_slot_resize_transport_failure_is_retryable(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        right.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        clients = [
            tiled_client(left, "0xleft", [0, 0], [500, 1000]),
            tiled_client(right, "0xright", [500, 0], [500, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=False),
            mock.patch.object(self.module, "hyprctl_json", return_value=None),
        ):
            self.assertIsNone(
                self.module.restore_tiled_slots(
                    [left, right], {1: "0xleft", 2: "0xright"}, clients
                )
            )

    def test_tiled_slot_resize_refresh_failure_marks_restore_failed(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=700, size_h=1000)
        right.update(at_x=700, at_y=0, size_w=300, size_h=1000)
        clients = [
            tiled_client(left, "0xleft", [0, 0], [500, 1000]),
            tiled_client(right, "0xright", [500, 0], [500, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=True),
            mock.patch.object(self.module, "hyprctl_json", return_value=None),
        ):
            self.assertIsNone(
                self.module.restore_tiled_slots(
                    [left, right], {1: "0xleft", 2: "0xright"}, clients
                )
            )

    def test_tiled_slot_restore_skips_ambiguous_identities(self):
        first = window(1, 0, "terminal", 10)
        second = window(2, 1, "terminal", 20)
        first.update(at_x=0, at_y=0, size_w=500, size_h=1000)
        second.update(at_x=500, at_y=0, size_w=500, size_h=1000)
        clients = [
            {
                "mapped": True,
                "address": "0xsecond",
                "class": "terminal",
                "initialClass": "terminal",
                "workspace": {"id": 1},
                "at": [0, 0],
                "size": [500, 1000],
                "floating": False,
                "fullscreen": 0,
            },
            {
                "mapped": True,
                "address": "0xfirst",
                "class": "terminal",
                "initialClass": "terminal",
                "workspace": {"id": 1},
                "at": [500, 0],
                "size": [500, 1000],
                "floating": False,
                "fullscreen": 0,
            },
        ]

        with mock.patch.object(self.module, "dispatch") as dispatch:
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [first, second], {1: "0xfirst", 2: "0xsecond"}, clients
                )
            )
        dispatch.assert_not_called()

        first["title"] = second["title"] = "Shared"
        for client in clients:
            client["title"] = "Shared"
        with mock.patch.object(self.module, "dispatch") as dispatch:
            self.assertTrue(
                self.module.restore_tiled_slots(
                    [first, second], {1: "0xfirst", 2: "0xsecond"}, clients
                )
            )
        dispatch.assert_not_called()

    def test_tiled_slot_failure_does_not_block_later_workspace(self):
        rows = []
        clients = []
        matches = {}
        for workspace_id in (1, 2):
            left = window(workspace_id * 10, 0, f"left-{workspace_id}", 10)
            right = window(workspace_id * 10 + 1, 1, f"right-{workspace_id}", 20)
            left.update(
                workspace_id=workspace_id, at_x=0, at_y=0, size_w=500, size_h=1000
            )
            right.update(
                workspace_id=workspace_id,
                at_x=500,
                at_y=0,
                size_w=500,
                size_h=1000,
            )
            rows.extend((left, right))
            matches.update(
                {
                    left["id"]: f"0xleft{workspace_id}",
                    right["id"]: f"0xright{workspace_id}",
                }
            )
            for row, x in ((right, 0), (left, 500)):
                clients.append(
                    {
                        "mapped": True,
                        "address": matches[row["id"]],
                        "class": row["class"],
                        "initialClass": row["initial_class"],
                        "workspace": {"id": workspace_id},
                        "at": [x, 0],
                        "size": [500, 1000],
                        "floating": False,
                        "fullscreen": 0,
                    }
                )

        with (
            mock.patch.object(
                self.module, "dispatch", side_effect=[False, True]
            ) as dispatch,
            mock.patch.object(
                self.module, "hyprctl_json", return_value=clients
            ) as hyprctl,
        ):
            self.assertFalse(self.module.restore_tiled_slots(rows, matches, clients))
        self.assertEqual(2, dispatch.call_count)
        hyprctl.assert_called_once_with("clients")

    def test_tiled_slot_swap_transport_failure_is_retryable(self):
        left = window(1, 0, "left", 10)
        right = window(2, 1, "right", 20)
        left.update(at_x=0, at_y=0, size_w=500, size_h=1000)
        right.update(at_x=500, at_y=0, size_w=500, size_h=1000)
        clients = [
            tiled_client(right, "0xright", [0, 0], [500, 1000]),
            tiled_client(left, "0xleft", [500, 0], [500, 1000]),
        ]

        with (
            mock.patch.object(self.module, "dispatch", return_value=False),
            mock.patch.object(self.module, "hyprctl_json", return_value=None),
        ):
            self.assertIsNone(
                self.module.restore_tiled_slots(
                    [left, right], {1: "0xleft", 2: "0xright"}, clients
                )
            )

    def test_discovery_rejects_class_only_window_on_another_workspace(self):
        row = window(1, 0, "terminal", 10, title="Saved")
        client = {
            "mapped": True,
            "address": "0xother",
            "class": "terminal",
            "initialClass": "terminal",
            "title": "Unrelated",
            "workspace": {"id": 2},
        }
        self.assertEqual(
            {},
            self.module.match_windows([row], [client], max_rank=3),
        )

    def test_fast_window_is_placed_while_slow_group_is_still_pending(self):
        rows = [
            window(1, 0, "slow-app", 10),
            window(2, 1, "fast-app", 20),
        ]
        events = []

        result, dispatch, place = self.run_restore(
            rows,
            wait_matches={2: "0x2"},
            appearances={2: 1},
            events=events,
        )

        self.assertEqual(1, result)
        self.assertEqual(2, dispatch.call_count)
        place.assert_called_once()
        self.assertEqual(
            ["initial-scan", "dispatch", "dispatch", "poll", "place:2"],
            events[:5],
        )

    def test_partial_process_group_waits_then_launches_once(self):
        rows = [
            window(1, 0, "chromium", 10, title="Browser"),
            window(2, 1, "slack-webapp", 10, title="Slack"),
        ]
        clients = [
            {
                "mapped": True,
                "address": "0x1",
                "class": "chromium",
                "initialClass": "chromium",
                "title": "Browser",
                "initialTitle": "",
                "workspace": {"id": 1},
            }
        ]
        result, dispatch, place = self.run_restore(
            rows,
            clients=clients,
            wait_matches={2: "0x2"},
        )
        self.assertEqual(0, result)
        self.assertEqual(1, dispatch.call_count)
        self.assertEqual(2, place.call_count)

    def test_dispatch_failure_returns_nonzero(self):
        result, dispatch, place = self.run_restore(
            [window(1, 0, "terminal", 10)], dispatch_result=False
        )
        self.assertEqual(1, result)
        self.assertEqual(1, dispatch.call_count)
        place.assert_not_called()

    def test_launch_failures_do_not_log_the_saved_command(self):
        secret = "credential=do-not-log"
        result = mock.Mock(returncode=1, stdout="", stderr=f"error: {secret}")
        with mock.patch.object(self.module.subprocess, "run", return_value=result):
            self.assertFalse(
                self.module.dispatch(
                    f"hl.dsp.exec_cmd([[app --token {secret}]])",
                    failure_context="restore: application launch",
                )
            )

        log_text = self.module.LOG_PATH.read_text()
        self.assertNotIn(secret, log_text)
        self.assertIn("request details redacted", log_text)

    def test_restore_timeout_does_not_log_the_saved_command(self):
        secret = "credential=do-not-log"
        row = window(1, 0, "terminal", 10)
        row["cmdline"] = f"/usr/bin/example --token {secret}"
        messages = []

        result, _, _ = self.run_restore([row], log_messages=messages)

        self.assertEqual(1, result)
        self.assertNotIn(secret, "\n".join(messages))
        self.assertIn("restore: no window appeared for terminal", messages)

    def test_monitor_move_failure_makes_restore_nonretryable(self):
        row = window(1, 0, "terminal", 10)
        row.update(at_x=0, at_y=0, size_w=1000, size_h=1000)
        tiled_matches = []
        result, _, place = self.run_restore(
            [row],
            {1: "0x1"},
            monitor_prepare_result=False,
            tiled_matches=tiled_matches,
        )
        self.assertEqual(1, result)
        place.assert_not_called()
        self.assertEqual([{}], tiled_matches)

    def test_monitor_ipc_failure_makes_restore_retryable(self):
        result, _, _ = self.run_restore(
            [window(1, 0, "terminal", 10)],
            {1: "0x1"},
            monitor_context=({}, {}, True),
        )
        self.assertEqual(75, result)

    def test_group_restore_ipc_failure_makes_restore_retryable(self):
        result, _, _ = self.run_restore(
            [window(1, 0, "terminal", 10)],
            {1: "0x1"},
            group_result=None,
        )
        self.assertEqual(75, result)

    def test_monitor_remap_runs_before_window_state_and_tiled_slot_restore(self):
        row = window(1, 0, "terminal", 10)
        row.update(at_x=0, at_y=0, size_w=1000, size_h=1000)
        events = []

        result, _, _ = self.run_restore(
            [row],
            {1: "0x1"},
            events=events,
            monitor_context=({1: "DP-1"}, {1: "eDP-1"}, False),
            track_monitor=True,
        )

        self.assertEqual(0, result)
        self.assertLess(events.index("monitor-prepare"), events.index("place:1"))
        self.assertLess(events.index("monitor-prepare"), events.index("tiled-restore"))

    def test_tiled_refresh_ipc_failure_makes_restore_retryable(self):
        row = window(1, 0, "terminal", 10)
        row.update(at_x=0, at_y=0, size_w=1000, size_h=1000)
        result, _, _ = self.run_restore([row], {1: "0x1"}, tiled_result=None)
        self.assertEqual(75, result)

    def test_initial_ipc_failure_returns_nonzero_without_dispatch(self):
        connection = mock.Mock()
        lock_file = mock.Mock()
        with (
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "db_conn", return_value=connection),
            mock.patch.object(
                self.module, "latest_session", return_value=(1, "periodic", "now")
            ),
            mock.patch.object(
                self.module, "load_windows", return_value=[window(1, 0, "terminal", 10)]
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=None),
            mock.patch.object(self.module, "dispatch") as dispatch,
        ):
            result = self.module.cmd_restore()
        self.assertEqual(75, result)
        dispatch.assert_not_called()

    def test_restore_uses_configured_timeout_and_monitor_fallback(self):
        self.module.persist_snapshot(
            "periodic", "complete", "", [window(1, 0, "terminal", 10)]
        )
        cfg = dict(self.module.DEFAULT_CONFIG)
        cfg.update(restore_timeout_seconds=37.5, monitor_fallback="DP-2")
        lock_file = mock.Mock()
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                clear=False,
            ),
            mock.patch.object(self.module, "load_config", return_value=cfg),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=[]),
            mock.patch.object(self.module, "restore_was_completed", return_value=False),
            mock.patch.object(
                self.module,
                "restore_windows",
                return_value=(1, 0, 1, False),
            ) as restore_windows,
            mock.patch.object(self.module, "mark_restore_completed", return_value=True),
        ):
            result = self.module.cmd_restore()

        self.assertEqual(0, result)
        args = restore_windows.call_args.args
        self.assertEqual(37.5, args[4])
        self.assertEqual("DP-2", args[5])

    def test_restore_places_windows_with_animations_suppressed(self):
        self.module.persist_snapshot(
            "periodic", "complete", "", [window(1, 0, "terminal", 10)]
        )
        lock_file = mock.Mock()
        events = []
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                clear=False,
            ),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=[]),
            mock.patch.object(self.module, "restore_was_completed", return_value=False),
            mock.patch.object(self.module, "animations_enabled", return_value=True),
            mock.patch.object(
                self.module,
                "set_animations_enabled",
                side_effect=lambda enabled: (
                    events.append(f"animations={enabled}") or True
                ),
            ),
            mock.patch.object(
                self.module,
                "restore_windows",
                side_effect=lambda *_a: events.append("restore") or (1, 0, 1, False),
            ),
            mock.patch.object(self.module, "mark_restore_completed", return_value=True),
        ):
            self.assertEqual(0, self.module.cmd_restore())

        self.assertEqual(["animations=False", "restore", "animations=True"], events)

    def test_dry_run_does_not_touch_animations(self):
        connection = mock.Mock()
        lock_file = mock.Mock()
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                clear=False,
            ),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "db_conn", return_value=connection),
            mock.patch.object(
                self.module, "latest_session", return_value=(1, "periodic", "now")
            ),
            mock.patch.object(
                self.module, "load_windows", return_value=[window(1, 0, "terminal", 10)]
            ),
            mock.patch.object(self.module, "hyprctl_json", return_value=[]),
            mock.patch.object(self.module, "set_animations_enabled") as keyword,
        ):
            self.assertEqual(0, self.module.cmd_restore(dry_run=True))
        keyword.assert_not_called()

    def test_no_session_without_compositor_requests_retry(self):
        connection = mock.Mock()
        lock_file = mock.Mock()
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "db_conn", return_value=connection),
            mock.patch.object(self.module, "latest_session", return_value=None),
            mock.patch.object(self.module, "hyprctl_json") as hyprctl,
        ):
            result = self.module.cmd_restore()
        self.assertEqual(75, result)
        hyprctl.assert_not_called()

    def test_second_operation_cannot_acquire_lock(self):
        first = self.module.acquire_operation_lock()
        second = self.module.acquire_operation_lock()
        first.close()
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_restore_lock_contention_requests_retry(self):
        with mock.patch.object(
            self.module, "acquire_operation_lock", return_value=None
        ):
            self.assertEqual(75, self.module.cmd_restore())

    def test_completed_restore_marker_skips_relaunch_in_same_desktop(self):
        connection = mock.Mock()
        lock_file = mock.Mock()
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}):
            self.module.mark_restore_completed(7)
            with (
                mock.patch.object(
                    self.module, "acquire_operation_lock", return_value=lock_file
                ),
                mock.patch.object(self.module, "db_conn", return_value=connection),
                mock.patch.object(
                    self.module, "latest_session", return_value=(7, "periodic", "now")
                ),
                mock.patch.object(self.module, "hyprctl_json", return_value=[]),
                mock.patch.object(self.module, "load_windows") as load_windows,
                mock.patch.object(self.module, "dispatch") as dispatch,
            ):
                result = self.module.cmd_restore()
        self.assertEqual(0, result)
        load_windows.assert_not_called()
        dispatch.assert_not_called()

    def test_completed_marker_still_requires_live_compositor(self):
        connection = mock.Mock()
        lock_file = mock.Mock()
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}):
            self.module.mark_restore_completed(7)
            with (
                mock.patch.object(
                    self.module, "acquire_operation_lock", return_value=lock_file
                ),
                mock.patch.object(self.module, "db_conn", return_value=connection),
                mock.patch.object(
                    self.module, "latest_session", return_value=(7, "periodic", "now")
                ),
                mock.patch.object(self.module, "hyprctl_json", return_value=None),
                mock.patch.object(self.module, "load_windows") as load_windows,
            ):
                result = self.module.cmd_restore()
        self.assertEqual(75, result)
        load_windows.assert_not_called()

    def test_dry_run_does_not_change_restore_marker(self):
        connection = mock.Mock()
        lock_file = mock.Mock()
        row = window(1, 0, "terminal", 10)
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                clear=False,
            ),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "db_conn", return_value=connection),
            mock.patch.object(
                self.module, "latest_session", return_value=(1, "periodic", "now")
            ),
            mock.patch.object(self.module, "load_windows", return_value=[row]),
            mock.patch.object(self.module, "hyprctl_json", return_value=[]),
            mock.patch.object(self.module, "mark_restore_completed") as mark,
        ):
            self.assertEqual(0, self.module.cmd_restore(dry_run=True))
        mark.assert_not_called()

    def test_empty_dry_run_does_not_change_restore_marker(self):
        connection = mock.Mock()
        lock_file = mock.Mock()
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                clear=False,
            ),
            mock.patch.object(
                self.module, "acquire_operation_lock", return_value=lock_file
            ),
            mock.patch.object(self.module, "db_conn", return_value=connection),
            mock.patch.object(
                self.module, "latest_session", return_value=(1, "periodic", "now")
            ),
            mock.patch.object(self.module, "load_windows", return_value=[]),
            mock.patch.object(self.module, "hyprctl_json", return_value=[]),
            mock.patch.object(self.module, "mark_restore_completed") as mark,
        ):
            self.assertEqual(0, self.module.cmd_restore(dry_run=True))
        mark.assert_not_called()

    def test_application_failure_remains_nonretryable_after_gate_is_written(self):
        with mock.patch.object(
            self.module,
            "mark_restore_completed",
            side_effect=[True, False],
        ) as mark:
            result, _, _ = self.run_restore(
                [window(1, 0, "terminal", 10)],
                dispatch_result=False,
            )
        self.assertEqual(1, result)
        mark.assert_called_once_with(1, complete=False)

    def test_restore_marker_write_failure_is_retryable(self):
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                clear=False,
            ),
            mock.patch.object(
                self.module.os, "replace", side_effect=OSError("disk full")
            ),
            mock.patch.object(self.module, "log"),
        ):
            self.assertFalse(self.module.mark_restore_completed(1))

    def test_autosave_sleeps_before_first_capture(self):
        with (
            mock.patch.object(
                self.module, "load_config", return_value={"autosave_seconds": 60}
            ),
            mock.patch.object(
                self.module.time, "sleep", side_effect=RuntimeError("stop")
            ) as sleep,
            mock.patch.object(self.module, "refresh_hyprland_instance"),
            mock.patch.object(self.module, "cmd_save") as save,
            self.assertRaisesRegex(RuntimeError, "stop"),
        ):
            self.module.cmd_autosave()
        sleep.assert_called_once_with(60)
        save.assert_not_called()

    def test_autosave_waits_for_restore_completion_marker(self):
        with (
            mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}),
            mock.patch.object(
                self.module, "load_config", return_value={"autosave_seconds": 60}
            ),
            mock.patch.object(
                self.module.time, "sleep", side_effect=[None, RuntimeError("stop")]
            ),
            mock.patch.object(self.module, "refresh_hyprland_instance"),
            mock.patch.object(self.module, "cmd_save") as save,
            self.assertRaisesRegex(RuntimeError, "stop"),
        ):
            self.module.cmd_autosave()
        save.assert_not_called()

    def test_autosave_keeps_running_when_configuration_becomes_invalid(self):
        with (
            mock.patch.object(
                self.module, "load_config", return_value={"autosave_seconds": 60}
            ),
            mock.patch.object(
                self.module.time,
                "sleep",
                side_effect=[None, None, RuntimeError("stop")],
            ),
            mock.patch.object(self.module, "refresh_hyprland_instance"),
            mock.patch.object(self.module, "restore_is_ready", return_value=True),
            mock.patch.object(
                self.module,
                "cmd_save",
                side_effect=self.module.ConfigError("invalid setting"),
            ) as save,
            mock.patch.object(self.module, "log"),
            self.assertRaisesRegex(RuntimeError, "stop"),
        ):
            self.module.cmd_autosave()
        self.assertEqual(2, save.call_count)

    def test_autosave_starts_with_defaults_when_the_config_is_invalid(self):
        self.write_config('{"autosave_seconds": 0}')
        with (
            mock.patch.object(
                self.module.time, "sleep", side_effect=RuntimeError("stop")
            ) as sleep,
            mock.patch.object(self.module, "log") as logged,
            self.assertRaisesRegex(RuntimeError, "stop"),
        ):
            self.module.cmd_autosave()
        sleep.assert_called_once_with(self.module.DEFAULT_CONFIG["autosave_seconds"])
        self.assertIn("autosave", logged.call_args_list[0].args[0])

    def test_autosave_is_not_ready_without_compositor_instance(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.module.restore_is_ready())

    def test_autosave_is_not_ready_after_incomplete_restore(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}):
            self.module.mark_restore_completed(7, complete=False)
            self.assertFalse(self.module.restore_is_ready())

    def test_autosave_refreshes_compositor_instance_from_user_manager(self):
        result = mock.Mock(
            returncode=0,
            stdout="HOME=/home/test\nHYPRLAND_INSTANCE_SIGNATURE=current-instance\n",
        )
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "stale-instance"},
                clear=False,
            ),
            mock.patch.object(
                self.module.subprocess, "run", return_value=result
            ) as run,
        ):
            self.assertTrue(self.module.refresh_hyprland_instance())
            self.assertEqual(
                "current-instance", os.environ["HYPRLAND_INSTANCE_SIGNATURE"]
            )
        run.assert_called_once_with(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_autosave_clears_stale_compositor_instance(self):
        result = mock.Mock(returncode=0, stdout="HOME=/home/test\n")
        with (
            mock.patch.dict(
                os.environ,
                {"HYPRLAND_INSTANCE_SIGNATURE": "stale-instance"},
                clear=False,
            ),
            mock.patch.object(self.module.subprocess, "run", return_value=result),
        ):
            self.assertFalse(self.module.refresh_hyprland_instance())
            self.assertNotIn("HYPRLAND_INSTANCE_SIGNATURE", os.environ)

    def test_mode_reports_enabled_autosave_as_active(self):
        results = [
            mock.Mock(returncode=0, stdout="enabled\n"),
            mock.Mock(returncode=0, stdout="active\n"),
        ]
        with (
            mock.patch.object(
                self.module.subprocess, "run", side_effect=results
            ) as run,
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(0, self.module.cmd_mode())
        self.assertEqual(
            [
                ["systemctl", "--user", "is-enabled", "omarchy-sesh-autosave.service"],
                ["systemctl", "--user", "is-active", "omarchy-sesh-autosave.service"],
            ],
            [call.args[0] for call in run.call_args_list],
        )
        output.assert_called_once_with("active")

    def test_mode_warns_when_enabled_autosave_is_not_running(self):
        results = [
            mock.Mock(returncode=0, stdout="enabled\n"),
            mock.Mock(returncode=3, stdout="failed\n"),
        ]
        stderr = io.StringIO()
        stdout = io.StringIO()
        with (
            mock.patch.object(self.module.subprocess, "run", side_effect=results),
            mock.patch("sys.stderr", stderr),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(0, self.module.cmd_mode())
        self.assertEqual("active", stdout.getvalue().strip())
        self.assertIn("enabled but failed", stderr.getvalue())
        self.assertIn("periodic saves are not happening", stderr.getvalue())

    def test_mode_reports_disabled_autosave_as_manual(self):
        result = mock.Mock(returncode=1, stdout="disabled\n", stderr="")
        with (
            mock.patch.object(self.module.subprocess, "run", return_value=result),
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(0, self.module.cmd_mode())
        output.assert_called_once_with("manual")

    def test_manual_mode_disables_autosave_now(self):
        result = mock.Mock(returncode=0)
        with mock.patch.object(
            self.module.subprocess, "run", return_value=result
        ) as run:
            self.assertEqual(0, self.module.cmd_mode("manual"))
        run.assert_called_once_with(
            [
                "systemctl",
                "--user",
                "disable",
                "--now",
                "omarchy-sesh-autosave.service",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_active_mode_enables_autosave_now(self):
        result = mock.Mock(returncode=0)
        with (
            mock.patch.object(self.module, "restore_is_ready", return_value=True),
            mock.patch.object(
                self.module.subprocess, "run", return_value=result
            ) as run,
        ):
            self.assertEqual(0, self.module.cmd_mode("active"))
        run.assert_called_once_with(
            ["systemctl", "--user", "enable", "--now", "omarchy-sesh-autosave.service"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_active_mode_captures_baseline_when_restore_is_not_ready(self):
        result = mock.Mock(returncode=0)
        with (
            mock.patch.object(
                self.module, "restore_is_ready", side_effect=[False, True]
            ),
            mock.patch.object(self.module, "cmd_save", return_value=0) as save,
            mock.patch.object(self.module.subprocess, "run", return_value=result),
        ):
            self.assertEqual(0, self.module.cmd_mode("active"))
        save.assert_called_once_with("manual", wait=True)

    def test_active_mode_stays_disabled_when_baseline_capture_fails(self):
        with (
            mock.patch.object(self.module, "restore_is_ready", return_value=False),
            mock.patch.object(self.module, "cmd_save", return_value=1),
            mock.patch.object(self.module.subprocess, "run") as run,
        ):
            self.assertEqual(1, self.module.cmd_mode("active"))
        run.assert_not_called()

    def test_active_mode_requires_baseline_marker(self):
        with (
            mock.patch.object(self.module, "restore_is_ready", return_value=False),
            mock.patch.object(self.module, "cmd_save", return_value=0),
            mock.patch.object(self.module.subprocess, "run") as run,
        ):
            self.assertEqual(1, self.module.cmd_mode("active"))
        run.assert_not_called()

    def run_installer(
        self,
        autosave_unit_exists,
        wrapped_menu=False,
        autosave_enabled=False,
        completed_install=None,
        config_name=".config",
        include_state_modes=False,
        permissive_state=False,
    ):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            config_home = home_path / config_name
            state_home = home_path / ".local" / "state"
            if permissive_state:
                state_dir = state_home / "omarchy"
                log_dir = state_dir / "log"
                log_dir.mkdir(parents=True)
                state_dir.chmod(0o755)
                log_dir.chmod(0o755)
                (state_dir / "session.db").write_text("database")
                (state_dir / "session.db").chmod(0o644)
                (log_dir / "omarchy-sesh.log").write_text("log")
                (log_dir / "omarchy-sesh.log").chmod(0o644)
            unit_dir = config_home / "systemd" / "user"
            if autosave_unit_exists:
                unit_dir.mkdir(parents=True)
                (unit_dir / "omarchy-sesh-autosave.service").write_text("existing\n")
            menu = config_home / "omarchy" / "extensions" / "omarchy-menu.jsonc"
            if wrapped_menu:
                menu.parent.mkdir(parents=True)
                menu.write_text('{"items": {"custom": {"label": "Custom"}}}\n')
            if completed_install is None:
                completed_install = autosave_unit_exists
            marker = state_home / "omarchy" / "sesh-installed"
            if completed_install:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("0.1.0\n")

            calls = home_path / "systemctl.calls"
            fake_systemctl = home_path / "systemctl"
            fake_systemctl.write_text(
                "#!/bin/sh\n"
                'printf \'%s\\n\' "$*" >>"$SYSTEMCTL_CALLS"\n'
                '[ "${2:-}" = is-enabled ] && exit "$AUTOSAVE_IS_ENABLED"\n'
                "exit 0\n"
            )
            fake_systemctl.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": home,
                    "XDG_CONFIG_HOME": str(config_home),
                    "XDG_STATE_HOME": str(state_home),
                    "SYSTEMCTL": str(fake_systemctl),
                    "SYSTEMCTL_CALLS": str(calls),
                    "AUTOSAVE_IS_ENABLED": "0" if autosave_enabled else "1",
                }
            )
            subprocess.run(
                ["bash", str(INSTALLER)],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            result = (
                calls.read_text().splitlines(),
                menu.read_text(),
                marker.read_text().strip(),
            )
            if include_state_modes:
                return result + (
                    {
                        "state": (state_home / "omarchy").stat().st_mode & 0o777,
                        "log": (state_home / "omarchy" / "log").stat().st_mode
                        & 0o777,
                        "install_marker": marker.stat().st_mode & 0o777,
                        "menu_marker": (
                            state_home / "omarchy" / "sesh-menu-created"
                        ).stat().st_mode
                        & 0o777,
                        "database": (state_home / "omarchy" / "session.db").stat().st_mode
                        & 0o777
                        if (state_home / "omarchy" / "session.db").exists()
                        else None,
                        "log_file": (
                            state_home / "omarchy" / "log" / "omarchy-sesh.log"
                        ).stat().st_mode
                        & 0o777
                        if (
                            state_home / "omarchy" / "log" / "omarchy-sesh.log"
                        ).exists()
                        else None,
                    },
                )
            return result

    def test_first_install_enables_autosave(self):
        calls, _, marker = self.run_installer(autosave_unit_exists=False)
        self.assertIn("--user enable omarchy-sesh-autosave.service", calls)
        self.assertEqual("0.2.5", marker)

    def test_installer_creates_owner_only_state(self):
        _, _, _, modes = self.run_installer(
            autosave_unit_exists=False, include_state_modes=True
        )
        self.assertEqual(
            {
                "state": 0o700,
                "log": 0o700,
                "install_marker": 0o600,
                "menu_marker": 0o600,
                "database": None,
                "log_file": None,
            },
            modes,
        )

    def test_installer_repairs_existing_permissive_state(self):
        _, _, _, modes = self.run_installer(
            autosave_unit_exists=False,
            include_state_modes=True,
            permissive_state=True,
        )
        self.assertEqual(0o700, modes["state"])
        self.assertEqual(0o700, modes["log"])
        self.assertEqual(0o600, modes["database"])
        self.assertEqual(0o600, modes["log_file"])

    def test_reinstall_preserves_manual_mode(self):
        calls, _, _ = self.run_installer(autosave_unit_exists=True)
        self.assertNotIn("--user enable omarchy-sesh-autosave.service", calls)

    def test_interrupted_install_recovers_active_mode(self):
        calls, _, _ = self.run_installer(
            autosave_unit_exists=True,
            completed_install=False,
        )
        self.assertIn("--user enable omarchy-sesh-autosave.service", calls)

    def test_update_restarts_running_autosave(self):
        calls, _, _ = self.run_installer(
            autosave_unit_exists=True,
            autosave_enabled=True,
        )
        self.assertIn("--user try-restart omarchy-sesh-autosave.service", calls)

    def test_installer_writes_actions_inside_wrapped_menu_items(self):
        _, menu, _ = self.run_installer(autosave_unit_exists=False, wrapped_menu=True)
        items = menu.index('"items"')
        begin = menu.index("// omarchy-sesh: begin power-menu overrides")
        custom = menu.index('"custom"')
        self.assertLess(items, begin)
        self.assertLess(begin, custom)

    def test_installer_honors_xdg_config_home(self):
        _, menu, _ = self.run_installer(
            autosave_unit_exists=False,
            config_name="xdg-config",
        )
        self.assertIn("omarchy-sesh: begin power-menu overrides", menu)

    def test_power_action_quotes_home_binary_path(self):
        _, menu, _ = self.run_installer(autosave_unit_exists=False)
        self.assertIn(r"\"$HOME/.local/bin/omarchy-sesh\" save", menu)

    def run_uninstaller(self, stop_status=0, active_status=3):
        temporary = tempfile.TemporaryDirectory()
        home = Path(temporary.name)
        config_home = home / "xdg-config"
        state_home = home / ".local" / "state"
        journal = state_home / "omarchy" / "session.db-journal"
        journal.parent.mkdir(parents=True)
        journal.write_text("private")
        binary = home / ".local" / "bin" / "omarchy-sesh"
        binary.parent.mkdir(parents=True)
        binary.symlink_to(home / "missing-binary")
        unit_dir = config_home / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "omarchy-sesh.service").symlink_to(home / "missing-unit")
        legacy_menu = home / ".config" / "omarchy" / "extensions" / "omarchy-menu.jsonc"
        legacy_menu.parent.mkdir(parents=True)
        legacy_menu.write_text(
            "{\n"
            "  // omarchy-sesh: begin power-menu overrides\n"
            '  "system.logout": {},\n'
            "  // omarchy-sesh: end power-menu overrides\n"
            "}\n"
        )
        fake_systemctl = home / "systemctl"
        fake_systemctl.write_text(
            "#!/bin/sh\n"
            '[ "${2:-}" = stop ] && exit "$STOP_STATUS"\n'
            '[ "${2:-}" = is-active ] && exit "$ACTIVE_STATUS"\n'
            "exit 0\n"
        )
        fake_systemctl.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_STATE_HOME": str(state_home),
                "SYSTEMCTL": str(fake_systemctl),
                "STOP_STATUS": str(stop_status),
                "ACTIVE_STATUS": str(active_status),
            }
        )
        result = subprocess.run(
            ["bash", str(UNINSTALLER)],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        return temporary, result, binary, unit_dir, legacy_menu

    def test_uninstall_removes_dangling_artifacts_and_legacy_menu(self):
        temporary, result, binary, unit_dir, legacy_menu = self.run_uninstaller()
        try:
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(binary.is_symlink())
            self.assertFalse((unit_dir / "omarchy-sesh.service").is_symlink())
            self.assertNotIn("omarchy-sesh", legacy_menu.read_text())
            self.assertFalse(
                (Path(temporary.name) / ".local/state/omarchy/session.db-journal").exists()
            )
        finally:
            temporary.cleanup()

    def test_uninstall_aborts_when_service_state_cannot_be_verified(self):
        temporary, result, binary, _, _ = self.run_uninstaller(
            stop_status=1,
            active_status=1,
        )
        try:
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(binary.is_symlink())
            self.assertIn("could not verify", result.stderr)
        finally:
            temporary.cleanup()

    def test_acceptance_reports_matching_power_menu_restore(self):
        temporary = tempfile.TemporaryDirectory()
        try:
            module = load_module(temporary.name)
            row = window(1, 0, "terminal", 10, title="acceptance")
            sid = module.persist_snapshot("logout", "complete", "", [row])
            module.RESTORE_MARKER_PATH.write_text(
                f'{{"session": {sid}, "instance": "instance-1", "complete": true}}'
            )
            client = tiled_client(row, "0x1", [0, 0], [1000, 1000])
            with (
                mock.patch.dict(
                    os.environ,
                    {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                    clear=False,
                ),
                mock.patch.object(module, "systemd_unit_active", return_value=True),
                mock.patch.object(module, "hyprctl_json", return_value=[client]),
                mock.patch.object(module, "subprocess") as subprocess_mock,
                mock.patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                subprocess_mock.run.return_value.returncode = 0
                result = module.cmd_acceptance(expect_power_save=True)
            self.assertEqual(0, result)
            self.assertNotIn("FAIL:", output.getvalue())
        finally:
            temporary.cleanup()

    def test_acceptance_reports_expected_restore_failure(self):
        temporary = tempfile.TemporaryDirectory()
        try:
            module = load_module(temporary.name)
            row = window(1, 0, "terminal", 10)
            sid = module.persist_snapshot("logout", "complete", "", [row])
            module.RESTORE_MARKER_PATH.write_text(
                f'{{"session": {sid}, "instance": "instance-1", "complete": false}}'
            )
            with (
                mock.patch.dict(
                    os.environ,
                    {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"},
                    clear=False,
                ),
                mock.patch.object(module, "systemd_unit_failed", return_value=True),
                mock.patch.object(module, "systemd_unit_active", return_value=True),
                mock.patch.object(module, "subprocess") as subprocess_mock,
                mock.patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                subprocess_mock.run.return_value.returncode = 0
                result = module.cmd_acceptance(expect_restore_failure=True)
            self.assertEqual(0, result)
            self.assertNotIn("FAIL:", output.getvalue())
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main()
