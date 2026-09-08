import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "controller" / "schedule_pause_controller.py"
SPEC = importlib.util.spec_from_file_location("downtown_controller", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

TEST_CONFIG = MODULE.VacuumConfig(
    vacuum_id="downtown",
    display_name="Downtown",
    schedule_name_prefix="Downtown Rock Schedule ",
    cleaning_service_name="Downtown Rock Cleaning",
    docked_service_name="Downtown Rock Docked",
    return_to_dock_service_name="Downtown Rock Return to Dock",
    state_file="/unused/downtown-pause-state.json",
    snapshot_file="/unused/downtown-pause-snapshot.json",
    backup_snapshot_file="/unused/downtown-pause-snapshot.backup.json",
    lock_file="/unused/downtown-pause.lock",
)
MODULE.configure(TEST_CONFIG)


def schedule(unique_id, on, name=None):
    return {
        "serviceName": name or f"Downtown Rock Schedule {unique_id}",
        "uniqueId": str(unique_id),
        "aid": 1,
        "iid": int(unique_id) if str(unique_id).isdigit() else 1,
        "on": on,
    }


def accessory(name, characteristic_type, value, *, writable=False, unique_id=None):
    permissions = ["ev", "pr"]
    if writable:
        permissions.append("pw")
    return {
        "serviceName": name,
        "uniqueId": unique_id or name.lower().replace(" ", "-"),
        "serviceCharacteristics": [
            {
                "type": characteristic_type,
                "value": value,
                "perms": permissions,
            }
        ],
    }


class ControllerTests(unittest.TestCase):
    def setUp(self):
        MODULE.configure(TEST_CONFIG)
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.paths = {
            "STATE_FILE": root / "state.json",
            "SNAPSHOT_FILE": root / "snapshot.json",
            "BACKUP_SNAPSHOT_FILE": root / "backup.json",
            "LOCK_FILE": root / "lock",
            "TOKEN_FILE": root / "legacy-token",
            "AUTO_AUTH_TOKEN_FILE": root / "auto-token",
            "AUTO_AUTH_CREDENTIALS_FILE": root / "credentials.json",
        }
        self.patchers = [
            mock.patch.object(MODULE, name, str(path))
            for name, path in self.paths.items()
        ]
        self.patchers.append(
            mock.patch.object(MODULE, "SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS", ())
        )
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.directory.cleanup()

    def write_json(self, key, data):
        self.paths[key].write_text(json.dumps(data), encoding="utf-8")

    def test_configuration_rejects_empty_values(self):
        invalid = MODULE.VacuumConfig(
            **{**TEST_CONFIG.__dict__, "vacuum_id": ""}
        )
        with self.assertRaisesRegex(ValueError, "non-empty strings"):
            MODULE.configure(invalid)

    def test_homebridge_configuration_selects_api_and_auth_paths(self):
        MODULE.configure_homebridge(
            base_url="http://homebridge.local:9999/",
            legacy_token_file="/private/legacy-token",
            token_file="/private/token",
            credentials_file="/private/credentials.json",
        )
        self.assertEqual(MODULE.BASE_URL, "http://homebridge.local:9999")
        self.assertEqual(MODULE.TOKEN_FILE, "/private/legacy-token")
        self.assertEqual(MODULE.AUTO_AUTH_TOKEN_FILE, "/private/token")
        self.assertEqual(
            MODULE.AUTO_AUTH_CREDENTIALS_FILE, "/private/credentials.json"
        )

    def test_configuration_selects_vacuum_identity_and_paths(self):
        uptown = MODULE.VacuumConfig(
            vacuum_id="uptown",
            display_name="Uptown",
            schedule_name_prefix="Uptown Rock Schedule ",
            cleaning_service_name="Uptown Rock Cleaning",
            docked_service_name="Uptown Rock Docked",
            return_to_dock_service_name="Uptown Rock Return to Dock",
            state_file="/runtime/uptown-state.json",
            snapshot_file="/runtime/uptown-snapshot.json",
            backup_snapshot_file="/runtime/uptown-backup.json",
            lock_file="/runtime/uptown.lock",
        )

        MODULE.configure(uptown)

        self.assertEqual(MODULE.VACUUM_ID, "uptown")
        self.assertEqual(MODULE.VACUUM_DISPLAY_NAME, "Uptown")
        self.assertEqual(MODULE.SCHEDULE_NAME_PREFIX, "Uptown Rock Schedule ")
        self.assertEqual(MODULE.STATE_FILE, "/runtime/uptown-state.json")

    def active_state(self, session="session-1"):
        return {
            "version": 1,
            "vacuumId": "downtown",
            "pauseActive": True,
            "sessionId": session,
            "updatedAt": "now",
        }

    def snapshot(self, schedules, session="session-1", version=2):
        snapshot = {
            "version": version,
            "vacuumId": "downtown",
            "sessionId": session,
            "createdAt": "now",
            "scheduleCount": len(schedules),
            "schedules": schedules,
        }
        if version == 3:
            snapshot["vacuumAction"] = {
                "result": "pending-schedule-disable",
                "updatedAt": "now",
            }
        return snapshot

    def saved(self, unique_id, pre, established):
        item = schedule(unique_id, pre)
        item["prePauseOn"] = pre
        item["pauseEstablishedOn"] = established
        return item

    def test_repeated_activation_preserves_active_snapshot(self):
        state = self.active_state()
        snapshot = self.snapshot([self.saved("1", True, False)], version=3)
        snapshot["vacuumAction"]["result"] = "not-required-not-cleaning"
        self.write_json("STATE_FILE", state)
        self.write_json("SNAPSHOT_FILE", snapshot)
        original = self.paths["SNAPSHOT_FILE"].read_text(encoding="utf-8")

        with mock.patch.object(MODULE, "discover_schedules") as discover:
            self.assertEqual(MODULE.activate_pause(), 0)
        discover.assert_not_called()
        self.assertEqual(
            self.paths["SNAPSHOT_FILE"].read_text(encoding="utf-8"), original
        )

    def test_repeated_activation_reconciles_incomplete_snapshot(self):
        self.write_json("STATE_FILE", self.active_state())
        self.write_json(
            "SNAPSHOT_FILE",
            self.snapshot([self.saved("1", True, True)], version=3),
        )

        with mock.patch.object(
            MODULE, "reconcile_active_pause", return_value=0
        ) as reconcile:
            self.assertEqual(MODULE.activate_pause(), 0)

        reconcile.assert_called_once_with()

    def test_activation_records_established_state_and_backup(self):
        initial = [schedule("1", True), schedule("2", False)]
        final = [schedule("1", False), schedule("2", False)]
        with mock.patch.object(
            MODULE, "discover_schedules", side_effect=[initial, final]
        ), mock.patch.object(MODULE, "set_schedule") as setter, mock.patch.object(
            MODULE, "handle_active_cleaning"
        ), mock.patch.object(
            MODULE, "session_id", return_value="session-1"
        ):
            self.assertEqual(MODULE.activate_pause(), 0)

        setter.assert_called_once_with(initial[0], False)
        snapshot = json.loads(self.paths["SNAPSHOT_FILE"].read_text(encoding="utf-8"))
        backup = json.loads(
            self.paths["BACKUP_SNAPSHOT_FILE"].read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot["version"], 3)
        self.assertEqual(
            [item["prePauseOn"] for item in snapshot["schedules"]], [True, False]
        )
        self.assertEqual(
            [item["pauseEstablishedOn"] for item in snapshot["schedules"]],
            [False, False],
        )
        self.assertEqual(snapshot, backup)

    def test_schedule_verification_retries_stale_discovery(self):
        target = schedule("1", True)
        stale = [schedule("1", True)]
        settled = [schedule("1", False)]

        with mock.patch.object(
            MODULE, "SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS", (1,)
        ), mock.patch.object(
            MODULE, "discover_schedules", side_effect=[stale, settled]
        ) as discover, mock.patch.object(MODULE.time, "sleep") as sleep:
            live, failures = MODULE.verify_schedule_changes([(target, False)])

        self.assertEqual(live, settled)
        self.assertEqual(failures, [])
        self.assertEqual(discover.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_schedule_verification_retries_transient_read_failure(self):
        target = schedule("1", True)
        settled = [schedule("1", False)]
        with mock.patch.object(
            MODULE, "SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS", (2,)
        ), mock.patch.object(
            MODULE,
            "discover_schedules",
            side_effect=[RuntimeError("no cloud response"), settled],
        ) as discover, mock.patch.object(MODULE.time, "sleep") as sleep:
            live, failures = MODULE.verify_schedule_changes([(target, False)])

        self.assertEqual(live, settled)
        self.assertEqual(failures, [])
        self.assertEqual(discover.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_discovery_recovery_is_bounded_and_reports_exhaustion(self):
        with mock.patch.object(
            MODULE, "SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS", (1, 2)
        ), mock.patch.object(
            MODULE, "discover_schedules", side_effect=RuntimeError("cloud silent")
        ) as discover, mock.patch.object(MODULE.time, "sleep") as sleep:
            with self.assertRaisesRegex(
                RuntimeError, "recovery exhausted after 3 attempts"
            ):
                MODULE.discover_schedules_with_recovery("Pause discovery")

        self.assertEqual(discover.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_atomic_runtime_json_is_mode_0600(self):
        path = self.paths["STATE_FILE"]
        path.write_text("old", encoding="utf-8")
        path.chmod(0o644)

        MODULE.write_json_atomic(str(path), {"safe": True})

        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")), {"safe": True}
        )

    def test_reconcile_completes_late_successful_active_pause(self):
        saved = [self.saved("1", True, True), self.saved("2", False, False)]
        snapshot = self.snapshot(saved, version=3)
        self.write_json("STATE_FILE", self.active_state())
        self.write_json("SNAPSHOT_FILE", snapshot)

        live = [schedule("1", False), schedule("2", False)]
        with mock.patch.object(
            MODULE, "discover_schedules", return_value=live
        ), mock.patch.object(MODULE, "handle_active_cleaning") as vacuum_action:
            self.assertEqual(MODULE.reconcile_active_pause(), 0)

        reconciled = json.loads(
            self.paths["SNAPSHOT_FILE"].read_text(encoding="utf-8")
        )
        backup = json.loads(
            self.paths["BACKUP_SNAPSHOT_FILE"].read_text(encoding="utf-8")
        )
        self.assertEqual(
            [item["pauseEstablishedOn"] for item in reconciled["schedules"]],
            [False, False],
        )
        self.assertEqual(reconciled, backup)
        vacuum_action.assert_called_once()

    def test_reconcile_resumes_disable_for_live_on_schedule(self):
        saved = [self.saved("1", True, True)]
        snapshot = self.snapshot(saved, version=3)
        self.write_json("STATE_FILE", self.active_state())
        self.write_json("SNAPSHOT_FILE", snapshot)
        with mock.patch.object(
            MODULE,
            "discover_schedules",
            side_effect=[[schedule("1", True)], [schedule("1", False)]],
        ), mock.patch.object(
            MODULE, "set_schedule"
        ) as setter, mock.patch.object(
            MODULE, "handle_active_cleaning"
        ) as vacuum_action:
            self.assertEqual(MODULE.reconcile_active_pause(), 0)

        setter.assert_called_once()
        self.assertEqual(setter.call_args.args[1], False)
        vacuum_action.assert_called_once()

    def test_abandon_clears_state_without_homebridge_access(self):
        self.write_json("STATE_FILE", self.active_state())
        snapshot = self.snapshot([self.saved("1", True, True)], version=3)
        self.write_json("SNAPSHOT_FILE", snapshot)

        with mock.patch.object(MODULE, "api_request") as api_request, mock.patch.object(
            MODULE, "discover_schedules"
        ) as discover, mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(
                MODULE.abandon_active_pause(
                    "I-WILL-RESTORE-SCHEDULES-MANUALLY"
                ),
                0,
            )

        api_request.assert_not_called()
        discover.assert_not_called()
        state = json.loads(self.paths["STATE_FILE"].read_text(encoding="utf-8"))
        self.assertFalse(state["pauseActive"])
        self.assertFalse(self.paths["SNAPSHOT_FILE"].exists())
        archives = list(
            self.paths["SNAPSHOT_FILE"].parent.glob(
                "snapshot.json.manual-recovery-*"
            )
        )
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text(encoding="utf-8")), snapshot)
        self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
        self.assertIn("No Homebridge or Roborock API request", output.getvalue())

    def test_abandon_requires_exact_confirmation_and_preserves_files(self):
        self.write_json("STATE_FILE", self.active_state())
        snapshot = self.snapshot([self.saved("1", True, True)], version=3)
        self.write_json("SNAPSHOT_FILE", snapshot)

        with self.assertRaisesRegex(RuntimeError, "manual recovery requires"):
            MODULE.abandon_active_pause("yes")

        self.assertTrue(MODULE.load_state()["pauseActive"])
        self.assertEqual(
            json.loads(self.paths["SNAPSHOT_FILE"].read_text(encoding="utf-8")),
            snapshot,
        )

    def test_v3_snapshot_metadata_is_validated(self):
        snapshot = self.snapshot([self.saved("1", True, False)], version=3)
        self.assertIs(MODULE.validate_snapshot(snapshot, "session-1"), snapshot)

        snapshot["vacuumAction"]["cleaningObserved"] = "yes"
        with self.assertRaisesRegex(RuntimeError, "cleaningObserved"):
            MODULE.validate_snapshot(snapshot, "session-1")

    def test_v1_and_v2_snapshots_remain_supported(self):
        self.assertEqual(MODULE.SUPPORTED_SNAPSHOT_VERSIONS, (1, 2, 3))
        for version in (1, 2):
            with self.subTest(version=version):
                snapshot = self.snapshot(
                    [self.saved("1", True, False)],
                    version=version,
                )
                self.assertIs(
                    MODULE.validate_snapshot(snapshot, "session-1"),
                    snapshot,
                )

    def test_idle_docked_vacuum_requires_no_action(self):
        snapshot = self.snapshot([], version=3)
        with mock.patch.object(
            MODULE,
            "discover_vacuum_state",
            return_value={"cleaning": False, "docked": True},
        ), mock.patch.object(MODULE, "trigger_return_to_dock") as trigger:
            MODULE.handle_active_cleaning(snapshot)

        trigger.assert_not_called()
        self.assertEqual(
            snapshot["vacuumAction"]["result"],
            "not-required-idle-docked",
        )

    def test_not_cleaning_away_from_dock_requires_no_action(self):
        snapshot = self.snapshot([], version=3)
        with mock.patch.object(
            MODULE,
            "discover_vacuum_state",
            return_value={"cleaning": False, "docked": False},
        ), mock.patch.object(MODULE, "trigger_return_to_dock") as trigger:
            MODULE.handle_active_cleaning(snapshot)

        trigger.assert_not_called()
        self.assertEqual(
            snapshot["vacuumAction"]["result"],
            "not-required-not-cleaning",
        )

    def test_cleaning_away_from_dock_finishes_after_action_acknowledgement(self):
        snapshot = self.snapshot([], version=3)
        action = {
            "serviceName": MODULE.RETURN_TO_DOCK_SERVICE_NAME,
            "uniqueId": "dock-action",
            "characteristic": {"type": "On", "value": 0, "perms": ["pr", "pw"]},
        }
        with mock.patch.object(
            MODULE,
            "discover_vacuum_state",
            return_value={
                "cleaning": True,
                "docked": False,
                "returnToDock": action,
            },
        ) as discover, mock.patch.object(
            MODULE, "trigger_return_to_dock"
        ) as trigger, mock.patch.object(MODULE.time, "sleep") as sleep:
            MODULE.handle_active_cleaning(snapshot)

        discover.assert_called_once_with(require_action=True)
        trigger.assert_called_once_with(action)
        sleep.assert_not_called()
        self.assertEqual(
            snapshot["vacuumAction"]["result"],
            "return-to-dock-acknowledged",
        )
        self.assertTrue(snapshot["vacuumAction"]["cleaningObserved"])
        self.assertIn("acknowledgedAt", snapshot["vacuumAction"])

    def test_simultaneous_cleaning_and_docked_fails_without_action(self):
        snapshot = self.snapshot([], version=3)
        with mock.patch.object(
            MODULE,
            "discover_vacuum_state",
            return_value={"cleaning": True, "docked": True},
        ), mock.patch.object(MODULE, "trigger_return_to_dock") as trigger:
            with self.assertRaisesRegex(RuntimeError, "simultaneously"):
                MODULE.handle_active_cleaning(snapshot)

        trigger.assert_not_called()
        self.assertEqual(
            snapshot["vacuumAction"]["result"],
            "inconsistent-sensors",
        )

    def test_action_write_failure_is_persisted(self):
        snapshot = self.snapshot([], version=3)
        action = {
            "serviceName": MODULE.RETURN_TO_DOCK_SERVICE_NAME,
            "uniqueId": "dock-action",
            "characteristic": {"type": "On", "value": 0, "perms": ["pr", "pw"]},
        }
        with mock.patch.object(
            MODULE,
            "discover_vacuum_state",
            return_value={
                "cleaning": True,
                "docked": False,
                "returnToDock": action,
            },
        ), mock.patch.object(
            MODULE,
            "trigger_return_to_dock",
            side_effect=RuntimeError("write failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                MODULE.handle_active_cleaning(snapshot)

        self.assertEqual(snapshot["vacuumAction"]["result"], "write-failed")

    def test_activation_vacuum_failure_retains_active_state_and_snapshot(self):
        initial = [schedule("1", True)]
        final = [schedule("1", False)]
        with mock.patch.object(
            MODULE,
            "discover_schedules",
            side_effect=[initial, final],
        ), mock.patch.object(MODULE, "set_schedule"), mock.patch.object(
            MODULE,
            "handle_active_cleaning",
            side_effect=RuntimeError("vacuum action failed"),
        ), mock.patch.object(
            MODULE, "session_id", return_value="session-1"
        ):
            self.assertEqual(MODULE.activate_pause(), 1)

        state = json.loads(self.paths["STATE_FILE"].read_text(encoding="utf-8"))
        self.assertTrue(state["pauseActive"])
        self.assertTrue(self.paths["SNAPSHOT_FILE"].exists())

    def test_missing_return_to_dock_action_is_a_discovery_failure(self):
        snapshot = self.snapshot([], version=3)
        with mock.patch.object(
            MODULE,
            "discover_vacuum_state",
            side_effect=RuntimeError("Return to Dock missing"),
        ), mock.patch.object(MODULE, "trigger_return_to_dock") as trigger:
            with self.assertRaisesRegex(RuntimeError, "missing"):
                MODULE.handle_active_cleaning(snapshot)

        trigger.assert_not_called()
        self.assertEqual(snapshot["vacuumAction"]["result"], "discovery-failed")

    def test_vacuum_service_validation_rejects_ambiguous_or_nonwritable_services(self):
        service = accessory("Example", "On", 0, writable=True)
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            MODULE.find_accessory_service([service, service], "Example", "On")

        readonly = accessory("Example", "On", 0)
        with self.assertRaisesRegex(RuntimeError, "not writable"):
            MODULE.find_accessory_service(
                [readonly],
                "Example",
                "On",
                writable=True,
            )

    def test_contact_sensor_rejects_invalid_value(self):
        service = {
            "serviceName": "Sensor",
            "characteristic": {"value": True},
        }
        with self.assertRaisesRegex(RuntimeError, "must be 0 or 1"):
            MODULE.contact_sensor_is_detected(service)

    def test_discovery_maps_contact_sensor_polarity_and_validates_action(self):
        accessories = [
            accessory(
                MODULE.CLEANING_SERVICE_NAME,
                "ContactSensorState",
                0,
            ),
            accessory(
                MODULE.DOCKED_SERVICE_NAME,
                "ContactSensorState",
                1,
            ),
            accessory(
                MODULE.RETURN_TO_DOCK_SERVICE_NAME,
                "On",
                0,
                writable=True,
                unique_id="dock-action",
            ),
        ]
        with mock.patch.object(MODULE, "get_accessories", return_value=accessories):
            state = MODULE.discover_vacuum_state(require_action=True)

        self.assertTrue(state["cleaning"])
        self.assertFalse(state["docked"])
        self.assertEqual(state["returnToDock"]["uniqueId"], "dock-action")

    def test_return_to_dock_writes_one_momentary_press(self):
        service = {
            "serviceName": MODULE.RETURN_TO_DOCK_SERVICE_NAME,
            "uniqueId": "dock-action",
        }
        with mock.patch.object(MODULE, "api_request", return_value=(200, None)) as api:
            MODULE.trigger_return_to_dock(service)

        api.assert_called_once_with(
            "PUT",
            "/api/accessories/dock-action",
            {"characteristicType": "On", "value": True},
        )

    def test_schedule_write_uses_extended_timeout(self):
        target = schedule("1", False)
        with mock.patch.object(
            MODULE,
            "api_request",
            return_value=(200, None),
        ) as api:
            self.assertTrue(MODULE.set_schedule(target, True))

        self.assertEqual(
            api.call_args_list[0],
            mock.call(
                "PUT",
                "/api/accessories/1",
                {"characteristicType": "On", "value": True},
                timeout=MODULE.SCHEDULE_WRITE_TIMEOUT_SECONDS,
            ),
        )
        self.assertEqual(api.call_count, 1)

    def test_schedule_batch_submits_all_changes_and_collects_failures(self):
        changes = [(schedule("1", False), True), (schedule("2", True), False)]

        def write(target, desired):
            if target["uniqueId"] == "2":
                raise RuntimeError("throttled")
            return desired

        with mock.patch.object(MODULE, "set_schedule", side_effect=write) as setter:
            failures = MODULE.set_schedules_batch(changes)

        self.assertEqual(setter.call_count, 2)
        self.assertEqual(failures, ["Downtown Rock Schedule 2: throttled"])

    def test_unpause_restores_only_pause_owned_state(self):
        saved = [
            self.saved("1", True, False),
            self.saved("2", False, False),
            self.saved("3", True, False),
        ]
        self.write_json("STATE_FILE", self.active_state())
        self.write_json("SNAPSHOT_FILE", self.snapshot(saved))
        live = [schedule("1", False), schedule("2", True), schedule("3", True)]

        final = [schedule("1", True), schedule("2", True), schedule("3", True)]
        with mock.patch.object(
            MODULE, "discover_schedules", side_effect=[live, final]
        ) as discover, mock.patch.object(MODULE, "set_schedule") as setter:
            self.assertEqual(MODULE.deactivate_pause(), 0)

        setter.assert_called_once()
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(setter.call_args.args[0]["uniqueId"], "1")
        self.assertIs(setter.call_args.args[1], True)
        state = json.loads(self.paths["STATE_FILE"].read_text(encoding="utf-8"))
        self.assertFalse(state["pauseActive"])
        self.assertFalse(self.paths["SNAPSHOT_FILE"].exists())

    def test_matching_backup_recovers_missing_primary(self):
        self.write_json("STATE_FILE", self.active_state())
        self.write_json(
            "BACKUP_SNAPSHOT_FILE", self.snapshot([self.saved("1", True, False)])
        )
        loaded = MODULE.load_active_snapshot(MODULE.load_state())
        self.assertEqual(loaded["sessionId"], "session-1")

    def test_unrelated_backup_is_rejected(self):
        self.write_json("STATE_FILE", self.active_state())
        self.write_json(
            "BACKUP_SNAPSHOT_FILE",
            self.snapshot([self.saved("1", True, False)], session="old-session"),
        )
        with self.assertRaisesRegex(RuntimeError, "No valid matching"):
            MODULE.load_active_snapshot(MODULE.load_state())

    def test_restore_failure_retains_active_state_and_snapshot(self):
        self.write_json("STATE_FILE", self.active_state())
        self.write_json("SNAPSHOT_FILE", self.snapshot([self.saved("1", True, False)]))
        with mock.patch.object(
            MODULE, "discover_schedules", return_value=[schedule("1", False)]
        ), mock.patch.object(
            MODULE, "set_schedule", side_effect=RuntimeError("write failed")
        ):
            self.assertEqual(MODULE.deactivate_pause(), 1)
        self.assertTrue(json.loads(self.paths["STATE_FILE"].read_text())["pauseActive"])
        self.assertTrue(self.paths["SNAPSHOT_FILE"].exists())

    def test_restore_batch_submits_every_required_write(self):
        saved = [
            self.saved("1", True, False),
            self.saved("2", True, False),
            self.saved("3", True, False),
        ]
        self.write_json("STATE_FILE", self.active_state())
        self.write_json("SNAPSHOT_FILE", self.snapshot(saved))
        live = [schedule("1", False), schedule("2", False), schedule("3", False)]

        with mock.patch.object(
            MODULE, "discover_schedules", return_value=live
        ), mock.patch.object(
            MODULE,
            "set_schedule",
            side_effect=RuntimeError("Roborock cloud timed out"),
        ) as setter:
            self.assertEqual(MODULE.deactivate_pause(), 1)

        self.assertEqual(setter.call_count, 3)
        self.assertCountEqual(
            setter.call_args_list,
            [
                mock.call(
                    {"serviceName": f"Downtown Rock Schedule {number}", "uniqueId": str(number)},
                    True,
                )
                for number in (1, 2, 3)
            ],
        )
        self.assertTrue(json.loads(self.paths["STATE_FILE"].read_text())["pauseActive"])
        self.assertTrue(self.paths["SNAPSHOT_FILE"].exists())

    def test_main_reports_expected_failure_without_traceback(self):
        error = io.StringIO()
        with mock.patch.object(
            MODULE.sys, "argv", [str(SCRIPT), "on"]
        ), mock.patch.object(
            MODULE,
            "activate_pause",
            side_effect=RuntimeError("HTTP 401 from GET /api/accessories"),
        ), mock.patch(
            "sys.stderr", error
        ):
            self.assertEqual(MODULE.main(), 1)

        output = error.getvalue()
        self.assertIn("ERROR: HTTP 401", output)
        self.assertIn("get-homebridge-token.sh", output)
        self.assertNotIn("Traceback", output)

    def test_api_request_refreshes_expired_token_and_retries_once(self):
        self.paths["AUTO_AUTH_TOKEN_FILE"].write_text("expired", encoding="utf-8")
        unauthorized = urllib.error.HTTPError(
            "http://localhost/api/accessories", 401, "Unauthorized", {}, io.BytesIO()
        )
        with mock.patch.object(
            MODULE,
            "api_request_with_token",
            side_effect=[unauthorized, (200, ["accessory"])],
        ) as request, mock.patch.object(
            MODULE, "refresh_access_token", return_value="replacement"
        ) as refresh:
            self.assertEqual(
                MODULE.api_request("GET", "/api/accessories"),
                (200, ["accessory"]),
            )

        refresh.assert_called_once_with()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[-2], "expired")
        self.assertEqual(request.call_args_list[1].args[-2], "replacement")
        self.assertEqual(
            request.call_args_list[0].args[-1], MODULE.API_READ_TIMEOUT_SECONDS
        )

    def test_api_request_does_not_refresh_non_authentication_error(self):
        self.paths["AUTO_AUTH_TOKEN_FILE"].write_text("token", encoding="utf-8")
        server_error = urllib.error.HTTPError(
            "http://localhost/api/accessories", 500, "Error", {}, io.BytesIO(b"bad")
        )
        with mock.patch.object(
            MODULE, "api_request_with_token", side_effect=server_error
        ), mock.patch.object(MODULE, "refresh_access_token") as refresh:
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                MODULE.api_request("GET", "/api/accessories")

        refresh.assert_not_called()

    def test_refresh_access_token_uses_credentials_and_replaces_cache(self):
        self.write_json(
            "AUTO_AUTH_CREDENTIALS_FILE",
            {"username": "automation", "password": "secret"},
        )
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"access_token": "new-token"}
        ).encode("utf-8")
        response.__enter__.return_value.status = 200

        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            self.assertEqual(MODULE.refresh_access_token(), "new-token")

        token_path = self.paths["AUTO_AUTH_TOKEN_FILE"]
        self.assertEqual(token_path.read_text(encoding="utf-8"), "new-token")
        self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)

    def test_main_rejects_unsupported_command_without_locking(self):
        error = io.StringIO()
        with mock.patch.object(
            MODULE.sys, "argv", [str(SCRIPT), "invalid"]
        ), mock.patch.object(MODULE, "operation_lock") as operation_lock, mock.patch(
            "sys.stderr", error
        ):
            self.assertEqual(MODULE.main(), 2)

        operation_lock.assert_not_called()
        self.assertIn("Unsupported command", error.getvalue())


if __name__ == "__main__":
    unittest.main()
