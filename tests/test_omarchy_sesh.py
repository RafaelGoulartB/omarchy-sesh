import importlib.machinery
import importlib.util
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


def load_module(state_home):
    with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state_home}, clear=False):
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


class OmarchySeshTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.module = load_module(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_lua_quote_uses_collision_free_long_string(self):
        value = "run 'quoted' ]] and ]=] command"
        quoted = self.module.lua_quote(value)
        self.assertTrue(quoted.startswith("[==["))
        self.assertTrue(quoted.endswith("]==]"))
        self.assertIn(value, quoted)

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
            """
        )
        conn.close()

        conn = self.module.db_conn()
        status = conn.execute(
            "SELECT capture_status FROM sessions WHERE id = 1"
        ).fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(windows)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()

        self.assertEqual("legacy_unknown", status)
        self.assertIn("pid", columns)
        self.assertIn("monitor_description", columns)
        self.assertEqual(3, version)

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

    def test_manual_snapshot_opens_autosave_gate(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "instance-1"}):
            self.assertEqual(
                0, self.module._save_clients("manual", [], [], "complete", [])
            )
            self.assertTrue(self.module.restore_is_ready())

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
                    [{"id": 0, "name": "DP-1", "description": "Dell Display"}],
                    "complete",
                    [],
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

        def run_dispatch(_lua):
            events.append("dispatch")
            return dispatch_result

        def run_place(row, _address, _current=None):
            events.append(f"place:{row['id']}")
            return place_result

        def advance(seconds):
            clock[0] += seconds

        def run_monitor_prepare(*args):
            if track_monitor:
                events.append("monitor-prepare")
            if monitor_prepare_result is not None:
                return monitor_prepare_result
            return prepare_workspace_monitor(*args)

        def run_tiled_restore(_rows, _matches, _clients):
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
            mock.patch.object(
                self.module,
                "hyprctl_json",
                side_effect=current_clients,
            ),
            mock.patch.object(
                self.module, "dispatch", side_effect=run_dispatch
            ) as dispatch,
            mock.patch.object(
                self.module.time, "monotonic", side_effect=lambda: clock[0]
            ),
            mock.patch.object(self.module.time, "sleep", side_effect=advance),
            mock.patch.object(self.module, "log"),
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
        result, dispatch, place = self.run_restore(
            rows,
            {1: "0x1", 2: "0x2"},
            appearances={1: 1, 2: 2},
        )
        self.assertEqual(0, result)
        self.assertEqual(2, dispatch.call_count)
        self.assertEqual(2, place.call_count)

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

    def test_tiled_slot_restore_does_not_resize_different_workspace_bounds(self):
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
        result = mock.Mock(returncode=0, stdout="enabled\n")
        with (
            mock.patch.object(
                self.module.subprocess, "run", return_value=result
            ) as run,
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(0, self.module.cmd_mode())
        run.assert_called_once_with(
            ["systemctl", "--user", "is-enabled", "omarchy-sesh-autosave.service"],
            capture_output=True,
            text=True,
            check=False,
        )
        output.assert_called_once_with("active")

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
    ):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            config_home = home_path / config_name
            state_home = home_path / ".local" / "state"
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
            return (
                calls.read_text().splitlines(),
                menu.read_text(),
                marker.read_text().strip(),
            )

    def test_first_install_enables_autosave(self):
        calls, _, marker = self.run_installer(autosave_unit_exists=False)
        self.assertIn("--user enable omarchy-sesh-autosave.service", calls)
        self.assertEqual("0.1.0", marker)

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


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    unittest.main()
