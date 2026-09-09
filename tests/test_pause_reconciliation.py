"""Exercise durable reconciliation against delayed/optimistic Homebridge state."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import test_schedule_pause_controller as fixtures
import test_all_vacuums_pause_controller as aggregate_fixtures
import test_public_installer as installer_fixtures


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ControllerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.engine = fixtures.MODULE
        self.now = 1000.0
        clock = mock.patch.object(self.engine.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        self.fixture.write_json("STATE_FILE", self.fixture.active_state())

    def start(self, desired=True, targets=None):
        snapshot = self.fixture.snapshot([
            self.fixture.saved("1", True, False),
            self.fixture.saved("2", False, False),
        ], version=3)
        snapshot["vacuumAction"]["result"] = "not-required-idle-docked"
        self.engine.persist_active_snapshot(snapshot)
        if targets is None:
            targets = {"1": False, "2": False} if desired else {"1": True}
        self.engine.begin_reconciliation(snapshot, desired, targets)
        return snapshot

    def snapshot(self):
        return self.engine.load_active_snapshot(self.engine.load_state())

    def tick(self, when, first=False, second=False):
        self.now = when
        with mock.patch.object(self.engine, "discover_schedules", return_value=[
            fixtures.schedule("1", first), fixtures.schedule("2", second)
        ]):
            return self.engine.maintain_pause()

    def test_immediate_matching_value_does_not_complete_operation(self):
        self.start()
        self.tick(1000)
        self.assertEqual(self.snapshot()["reconciliation"]["phase"], "settling")
        self.tick(1001)
        self.assertEqual(self.snapshot()["reconciliation"]["samples"], 1)
        self.tick(1060)
        self.tick(1120)
        self.assertEqual(self.snapshot()["reconciliation"]["phase"], "complete")

    def test_delayed_rollback_retries_only_mismatched_schedule_after_cooldown(self):
        snapshot = self.start()
        self.engine.record_reconciliation_attempt(snapshot, [(fixtures.schedule("1", True), False)])
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.tick(1000)
            self.tick(1060, first=True)
            writes.assert_not_called()
            self.tick(1120, first=True)
            self.assertEqual([(s["uniqueId"], desired) for s, desired in writes.call_args.args[0]], [("1", False)])
            self.assertEqual(self.snapshot()["reconciliation"]["attempts"]["1"], 2)

    def test_write_budget_is_durable_but_late_success_can_still_settle(self):
        self.start()
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            for when in (1000, 1120, 1240, 1360, 1480):
                self.tick(when, first=True)
            self.assertEqual(writes.call_count, 3)
            self.assertEqual(self.snapshot()["reconciliation"]["phase"], "needs-attention")
            for when in (1540, 1600, 1660):
                self.tick(when)
            self.assertEqual(writes.call_count, 3)
            self.assertEqual(self.snapshot()["reconciliation"]["phase"], "complete")

    def test_unpause_retains_snapshot_and_heals_visible_status_after_late_rollback(self):
        self.start(False)
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.tick(1000, first=True)
            self.assertTrue(self.engine.load_state()["pauseActive"])
            self.assertFalse(self.engine.display_pause_state(self.engine.load_state()))
            self.tick(1060, first=True)
            self.tick(1120, first=True)
            self.assertFalse(self.engine.load_state()["pauseActive"])
            self.assertTrue(self.fixture.paths["SNAPSHOT_FILE"].exists())
            self.tick(1420, first=False)
            self.assertEqual(self.engine.load_state()["operation"]["phase"], "needs-attention")
            self.assertTrue(self.engine.display_pause_state(self.engine.load_state()))
            writes.assert_not_called()  # a settled operation never overrides a manual edit

    def test_settled_pause_audits_less_often_and_preserves_manual_changes(self):
        self.start()
        for when in (1000, 1060, 1120):
            self.tick(when)
        self.now = 1180
        with mock.patch.object(self.engine, "discover_schedules") as reads:
            self.engine.maintain_pause()
            reads.assert_not_called()
        with mock.patch.object(self.engine, "set_schedules_batch") as writes:
            self.tick(1420, first=True)
            self.assertFalse(self.engine.display_pause_state(self.engine.load_state()))
            self.tick(1720, first=True)
            writes.assert_not_called()

    def test_read_outage_breaks_settlement_and_preserves_journal(self):
        self.start()
        self.tick(1000)
        self.now = 1060
        with mock.patch.object(self.engine, "discover_schedules", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.maintain_pause()
        self.assertEqual(self.snapshot()["reconciliation"]["samples"], 0)
        self.tick(1120)
        self.assertEqual(self.snapshot()["reconciliation"]["phase"], "settling")

    def test_long_restart_gap_does_not_count_as_stable_observation(self):
        self.start()
        self.tick(1000)
        self.tick(1060)
        self.tick(2000)
        self.assertEqual(self.snapshot()["reconciliation"]["samples"], 1)
        self.assertEqual(self.snapshot()["reconciliation"]["phase"], "settling")

    def test_changed_membership_prevents_automatic_writes(self):
        self.start()
        with mock.patch.object(self.engine, "discover_schedules", return_value=[fixtures.schedule("1", True)]), mock.patch.object(self.engine, "set_schedules_batch") as writes:
            with self.assertRaisesRegex(RuntimeError, "membership"):
                self.engine.maintain_pause()
            writes.assert_not_called()
        self.assertTrue(self.fixture.paths["SNAPSHOT_FILE"].exists())

    def test_crash_after_attempt_record_does_not_immediately_replay_write(self):
        self.start()
        with mock.patch.object(self.engine, "set_schedules_batch", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.tick(1000, first=True)
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.tick(1060, first=True)
            writes.assert_not_called()
            self.tick(1120, first=True)
            self.assertEqual(writes.call_count, 1)
        self.assertEqual(self.snapshot()["reconciliation"]["attempts"]["1"], 2)

    def test_off_supersedes_unfinished_pause_even_when_discovery_fails(self):
        self.start()
        with mock.patch.object(self.engine, "discover_schedules", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.deactivate_pause()
        operation = self.snapshot()["reconciliation"]
        self.assertFalse(operation["desiredPause"])
        self.assertEqual(operation["phase"], "planning")
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.tick(1060, first=False)
            self.assertEqual([(s["uniqueId"], desired) for s, desired in writes.call_args.args[0]], [("1", True)])

    def test_off_watches_for_late_disable_even_if_switch_already_looks_restored(self):
        self.start()
        with mock.patch.object(self.engine, "discover_schedules", return_value=[fixtures.schedule("1", True), fixtures.schedule("2", False)]), mock.patch.object(self.engine, "set_schedules_batch", return_value=[]):
            self.engine.deactivate_pause()
        self.assertEqual(self.snapshot()["reconciliation"]["targets"], {"1": True, "2": False})
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.tick(1120, first=False)
            self.assertEqual(writes.call_args.args[0][0][1], True)

    def test_initial_discovery_outage_can_be_cancelled_without_snapshot_or_writes(self):
        self.engine.persist_state(False, "initial")
        with mock.patch.object(self.engine, "discover_schedules", side_effect=RuntimeError("offline")), mock.patch.object(self.engine, "set_schedules_batch") as writes:
            with self.assertRaises(RuntimeError):
                self.engine.activate_pause()
            self.assertTrue(self.engine.load_state()["pendingActivation"])
            self.engine.deactivate_pause()
            self.assertFalse(self.engine.display_pause_state(self.engine.load_state()))
            self.engine.maintain_pause()
            writes.assert_not_called()

    def test_initial_discovery_outage_resumes_from_durable_request(self):
        self.engine.persist_state(False, "initial")
        with mock.patch.object(self.engine, "discover_schedules", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.activate_pause()
        with mock.patch.object(self.engine, "activate_pause", return_value=0) as resume:
            self.engine.maintain_pause()
            resume.assert_called_once()

    def test_read_only_legacy_adoption_never_replays_docking_or_schedule_commands(self):
        snapshot = self.start()
        snapshot.pop("reconciliation")
        self.engine.persist_active_snapshot(snapshot)
        self.engine.persist_state(True, snapshot["sessionId"])
        with mock.patch.object(self.engine, "set_schedules_batch") as writes, mock.patch.object(self.engine, "handle_active_cleaning") as dock:
            self.tick(1000, first=True)
            writes.assert_not_called()
            dock.assert_not_called()

    def test_ambiguous_dock_request_is_not_replayed(self):
        snapshot = self.start()
        snapshot["vacuumAction"]["result"] = "return-to-dock-requested"
        self.engine.persist_active_snapshot(snapshot)
        with mock.patch.object(self.engine, "handle_active_cleaning") as dock:
            self.tick(1000)
            self.tick(1060)
            dock.assert_not_called()

    def test_invalid_retry_journal_fails_closed(self):
        snapshot = self.start()
        snapshot["reconciliation"]["attempts"]["1"] = -1
        self.engine.persist_active_snapshot(snapshot)
        with mock.patch.object(self.engine, "set_schedules_batch") as writes:
            with self.assertRaisesRegex(RuntimeError, "No valid"):
                self.engine.maintain_pause()
            writes.assert_not_called()

    def test_retained_settled_restore_does_not_block_upgrade_but_pending_restore_does(self):
        self.start(False)
        installer = installer_fixtures.MODULE
        path = self.fixture.paths["SNAPSHOT_FILE"]
        self.assertTrue(installer.unresolved_snapshot(path, self.engine.load_state()))
        for when in (1000, 1060, 1120):
            self.tick(when, first=True)
        self.assertFalse(installer.unresolved_snapshot(path, self.engine.load_state()))
        self.tick(1420, first=False)
        self.assertTrue(installer.unresolved_snapshot(path, self.engine.load_state()))

    def test_maintenance_cli_routes_through_existing_lock(self):
        with mock.patch.object(self.engine.sys, "argv", ["controller", "maintain"]), mock.patch.object(self.engine, "maintain_pause", return_value=0) as maintain:
            self.assertEqual(self.engine.main(), 0)
            maintain.assert_called_once()


class AggregateMaintenanceTests(unittest.TestCase):
    def test_failure_for_one_vacuum_does_not_skip_the_other(self):
        engine = aggregate_fixtures.MODULE
        with mock.patch.object(engine, "ROOT", "/test-runtime"), mock.patch.object(engine, "VACUUMS", ({"id": "one"}, {"id": "two"})), mock.patch.object(engine.subprocess, "run", side_effect=[mock.Mock(returncode=1), mock.Mock(returncode=0)]) as run:
            self.assertEqual(engine.maintain_all(), 1)
            self.assertEqual(run.call_count, 2)
            self.assertTrue(all(call.args[0][-1] == "maintain" for call in run.call_args_list))
