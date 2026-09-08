import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = (
    Path(__file__).parents[1]
    / "controller"
    / "all-vacuums-pause-controller.py"
)
SPEC = importlib.util.spec_from_file_location("all_vacuums_controller", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AllVacuumsControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.states = {
            "downtown": root / "downtown.json",
            "uptown": root / "uptown.json",
        }
        self.snapshots = {
            "downtown": root / "downtown-snapshot.json",
            "uptown": root / "uptown-snapshot.json",
        }
        self.vacuums = (
            {
                "id": "downtown",
                "displayName": "Downtown",
                "stateFile": str(self.states["downtown"]),
                "snapshotFile": str(self.snapshots["downtown"]),
                "onCommand": "/commands/downtown-on",
                "offCommand": "/commands/downtown-off",
            },
            {
                "id": "uptown",
                "displayName": "Uptown",
                "stateFile": str(self.states["uptown"]),
                "snapshotFile": str(self.snapshots["uptown"]),
                "onCommand": "/commands/uptown-on",
                "offCommand": "/commands/uptown-off",
            },
        )
        self.patchers = (
            mock.patch.object(MODULE, "VACUUMS", self.vacuums),
            mock.patch.object(MODULE, "LOCK_FILE", str(root / "all.lock")),
            mock.patch.object(
                MODULE,
                "OPERATION_LOG_FILE",
                str(root / "all-vacuums-operation.log"),
            ),
        )
        for patcher in self.patchers:
            patcher.start()
        self.write_state("downtown", False)
        self.write_state("uptown", False)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.directory.cleanup()

    def write_state(self, vacuum_id, active):
        self.states[vacuum_id].write_text(
            json.dumps(
                {
                    "version": 1,
                    "vacuumId": vacuum_id,
                    "pauseActive": active,
                    "sessionId": f"{vacuum_id}-session",
                }
            ),
            encoding="utf-8",
        )
        if active:
            self.snapshots[vacuum_id].write_text(
                json.dumps(
                    {
                        "version": 3,
                        "vacuumId": vacuum_id,
                        "sessionId": f"{vacuum_id}-session",
                        "scheduleCount": 1,
                        "schedules": [
                            {
                                "serviceName": f"{vacuum_id} schedule",
                                "uniqueId": f"{vacuum_id}-schedule",
                                "prePauseOn": True,
                                "pauseEstablishedOn": False,
                            }
                        ],
                        "vacuumAction": {
                            "result": "not-required-idle-docked",
                        },
                    }
                ),
                encoding="utf-8",
            )

    def command_runner(self, failures=()):
        failures = set(failures)

        def run(vacuum, command_key):
            desired = command_key == "onCommand"
            if vacuum["id"] not in failures:
                self.write_state(vacuum["id"], desired)
            return (
                vacuum,
                MODULE.subprocess.CompletedProcess(
                    [vacuum[command_key]],
                    1 if vacuum["id"] in failures else 0,
                    f"{vacuum['displayName']} output\n",
                    "",
                ),
            )

        return run

    def test_combined_state_is_on_when_any_vacuum_is_paused(self):
        self.assertFalse(MODULE.combined_state(MODULE.read_states()))
        self.write_state("downtown", True)
        self.assertTrue(MODULE.combined_state(MODULE.read_states()))
        self.write_state("uptown", True)
        self.assertTrue(MODULE.combined_state(MODULE.read_states()))

    def test_state_command_outputs_exact_normalized_boolean(self):
        with mock.patch("sys.argv", [str(SCRIPT), "state"]), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            self.assertEqual(MODULE.main(), 0)

        self.assertEqual(output.getvalue(), "false\n")

    def test_dispatch_returns_after_starting_detached_worker(self):
        process = mock.Mock(pid=4321)
        with mock.patch.object(
            MODULE.subprocess,
            "Popen",
            return_value=process,
        ) as popen, mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(MODULE.dispatch_set_all(True), 0)

        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0][-1], "on")
        self.assertIs(kwargs["stdin"], MODULE.subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], MODULE.subprocess.STDOUT)
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])
        self.assertIn("accepted", output.getvalue())
        self.assertIn("4321", output.getvalue())
        self.assertEqual(
            os.stat(MODULE.OPERATION_LOG_FILE).st_mode & 0o777,
            0o600,
        )

    def test_activate_runs_both_vacuums_and_verifies_active(self):
        with mock.patch.object(
            MODULE,
            "run_vacuum_command",
            side_effect=self.command_runner(),
        ) as runner, mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(MODULE.set_all(True), 0)

        self.assertEqual(runner.call_count, 2)
        self.assertTrue(MODULE.combined_state(MODULE.read_states()))

    def test_activate_reconciles_active_in_progress_vacuum(self):
        self.write_state("downtown", True)
        snapshot = json.loads(
            self.snapshots["downtown"].read_text(encoding="utf-8")
        )
        snapshot["vacuumAction"]["result"] = "pending-schedule-disable"
        snapshot["schedules"][0]["pauseEstablishedOn"] = True
        self.snapshots["downtown"].write_text(json.dumps(snapshot), encoding="utf-8")

        with mock.patch.object(
            MODULE,
            "run_vacuum_command",
            side_effect=self.command_runner(),
        ) as runner, mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(MODULE.set_all(True), 0)

        runner.assert_any_call(self.vacuums[0], "onCommand")
        self.assertIn("reconciling the existing transaction", output.getvalue())

    def test_deactivate_skips_already_inactive_vacuum(self):
        self.write_state("downtown", True)
        with mock.patch.object(
            MODULE,
            "run_vacuum_command",
            side_effect=self.command_runner(),
        ) as runner, mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(MODULE.set_all(False), 0)

        runner.assert_called_once_with(self.vacuums[0], "offCommand")
        self.assertFalse(MODULE.combined_state(MODULE.read_states()))

    def test_partial_failure_is_reported_and_remains_recoverable(self):
        with mock.patch.object(
            MODULE,
            "run_vacuum_command",
            side_effect=self.command_runner(failures={"uptown"}),
        ), mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(MODULE.set_all(True), 1)

        self.assertIn("partially applied", output.getvalue())
        states = MODULE.read_states()
        self.assertTrue(states["downtown"])
        self.assertFalse(states["uptown"])
        self.assertTrue(MODULE.combined_state(states))

    def test_expiration_skips_when_setting_is_disabled(self):
        with mock.patch.object(
            MODULE, "read_enabled", return_value=False
        ), mock.patch.object(MODULE, "set_all") as set_all, mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            self.assertEqual(MODULE.expire_pauses(), 0)

        set_all.assert_not_called()
        self.assertIn("DISABLED", output.getvalue())

    def test_expiration_deactivates_active_vacuums_when_enabled(self):
        with mock.patch.object(
            MODULE, "read_enabled", return_value=True
        ), mock.patch.object(MODULE, "set_all", return_value=0) as set_all, mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ):
            self.assertEqual(MODULE.expire_pauses(), 0)

        set_all.assert_called_once_with(False)

    def test_invalid_state_stops_before_commands(self):
        self.states["uptown"].write_text("{}", encoding="utf-8")
        with mock.patch.object(MODULE, "run_vacuum_command") as runner:
            with self.assertRaisesRegex(RuntimeError, "schema or vacuum ID"):
                MODULE.set_all(True)
        runner.assert_not_called()

    def test_active_state_accepts_in_progress_snapshot(self):
        self.write_state("uptown", True)
        snapshot = json.loads(self.snapshots["uptown"].read_text(encoding="utf-8"))
        snapshot["vacuumAction"]["result"] = "pending-schedule-disable"
        self.snapshots["uptown"].write_text(json.dumps(snapshot), encoding="utf-8")

        self.assertEqual(
            MODULE.validate_active_snapshot(
                self.vacuums[1], MODULE.load_vacuum_state(self.vacuums[1])
            ),
            "in-progress",
        )
        self.assertTrue(MODULE.read_states()["uptown"])

    def test_active_state_accepts_unconfirmed_schedule_while_pending(self):
        self.write_state("uptown", True)
        snapshot = json.loads(self.snapshots["uptown"].read_text(encoding="utf-8"))
        snapshot["schedules"][0]["pauseEstablishedOn"] = True
        self.snapshots["uptown"].write_text(json.dumps(snapshot), encoding="utf-8")

        self.assertTrue(MODULE.read_states()["uptown"])

    def test_active_state_rejects_corrupt_snapshot_fields(self):
        self.write_state("uptown", True)
        snapshot = json.loads(self.snapshots["uptown"].read_text(encoding="utf-8"))
        snapshot["schedules"][0]["pauseEstablishedOn"] = "no"
        self.snapshots["uptown"].write_text(json.dumps(snapshot), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "pauseEstablishedOn is invalid"):
            MODULE.read_states()

    def test_active_state_classifies_operational_failure_as_recovery_required(self):
        self.write_state("uptown", True)
        state = MODULE.load_vacuum_state(self.vacuums[1])
        snapshot = json.loads(self.snapshots["uptown"].read_text(encoding="utf-8"))
        snapshot["vacuumAction"]["result"] = "transition-timeout"
        self.snapshots["uptown"].write_text(json.dumps(snapshot), encoding="utf-8")

        self.assertEqual(
            MODULE.validate_active_snapshot(self.vacuums[1], state),
            "recovery-required",
        )
        self.assertTrue(MODULE.read_states()["uptown"])

    def test_active_state_accepts_acknowledged_return_without_dock_arrival(self):
        self.write_state("uptown", True)
        state = MODULE.load_vacuum_state(self.vacuums[1])
        snapshot = json.loads(self.snapshots["uptown"].read_text(encoding="utf-8"))
        snapshot["vacuumAction"]["result"] = "return-to-dock-acknowledged"
        self.snapshots["uptown"].write_text(json.dumps(snapshot), encoding="utf-8")

        self.assertEqual(
            MODULE.validate_active_snapshot(self.vacuums[1], state),
            "complete",
        )


if __name__ == "__main__":
    unittest.main()
