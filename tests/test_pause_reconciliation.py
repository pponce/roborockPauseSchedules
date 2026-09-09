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
        self.engine.REQUEST_WINDOW = None
        self.addCleanup(setattr, self.engine, "REQUEST_WINDOW", None)
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

    # Exercise the later retry/audit path with an injected longer budget.
    @mock.patch.object(fixtures.MODULE, "VERIFICATION_WINDOW_SECONDS", 600)
    def test_write_budget_is_durable_but_late_success_can_still_settle(self):
        self.start()
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            for when in (1000, 1120, 1240, 1360):
                self.tick(when, first=True)
            self.assertEqual(writes.call_count, 3)
            self.assertEqual(self.snapshot()["reconciliation"]["phase"], "needs-attention")
            for when in (1420, 1480, 1540):
                self.tick(when)
            self.assertEqual(writes.call_count, 3)
            self.assertEqual(self.snapshot()["reconciliation"]["phase"], "complete")

    # Exercise the later retry/audit path with an injected longer budget.
    @mock.patch.object(fixtures.MODULE, "VERIFICATION_WINDOW_SECONDS", 600)
    def test_unpause_retains_snapshot_and_heals_visible_status_after_late_rollback(self):
        self.start(False)
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.tick(1000, first=True)
            self.assertTrue(self.engine.load_state()["pauseActive"])
            self.assertFalse(self.engine.display_pause_state(self.engine.load_state()))
            self.tick(1060, first=True)
            for when in (1120, 1180, 1240, 1300, 1360):
                self.tick(when, first=True)
            self.assertFalse(self.engine.load_state()["pauseActive"])
            self.assertTrue(self.fixture.paths["SNAPSHOT_FILE"].exists())
            self.tick(1420, first=False)
            self.assertEqual(self.engine.load_state()["operation"]["phase"], "needs-attention")
            self.assertTrue(self.engine.display_pause_state(self.engine.load_state()))
            writes.assert_not_called()  # a settled operation never overrides a manual edit

    # Exercise the later retry/audit path with an injected longer budget.
    @mock.patch.object(fixtures.MODULE, "VERIFICATION_WINDOW_SECONDS", 600)
    def test_settled_pause_throttles_observations_and_preserves_manual_changes(self):
        self.start()
        for when in (1000, 1060, 1120, 1180, 1240, 1300, 1360):
            self.tick(when)
        self.now = 1390
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
        self.now = 2000
        with mock.patch.object(self.engine, "discover_schedules") as reads:
            self.engine.maintain_pause()
            reads.assert_not_called()
        self.assertTrue(self.snapshot()["reconciliation"]["window"]["closed"])
        self.assertEqual(self.snapshot()["reconciliation"]["phase"], "needs-attention")

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

    # Exercise the later retry/audit path with an injected longer budget.
    @mock.patch.object(fixtures.MODULE, "VERIFICATION_WINDOW_SECONDS", 600)
    def test_retained_settled_restore_does_not_block_upgrade_but_pending_restore_does(self):
        self.start(False)
        installer = installer_fixtures.MODULE
        path = self.fixture.paths["SNAPSHOT_FILE"]
        self.assertTrue(installer.unresolved_snapshot(path, self.engine.load_state()))
        for when in (1000, 1060, 1120, 1180, 1240, 1300, 1360):
            self.tick(when, first=True)
        self.assertFalse(installer.unresolved_snapshot(path, self.engine.load_state()))
        self.tick(1420, first=False)
        self.assertTrue(installer.unresolved_snapshot(path, self.engine.load_state()))

    def test_maintenance_cli_routes_through_existing_lock(self):
        with mock.patch.object(self.engine.sys, "argv", ["controller", "maintain"]), mock.patch.object(self.engine, "maintain_pause", return_value=0) as maintain:
            self.assertEqual(self.engine.main(), 0)
            maintain.assert_called_once()

    def test_deadline_stops_reads_writes_and_docking_for_pause_and_restore(self):
        for desired in (True, False):
            self.now = 1000
            self.start(desired)
            self.now = 1180
            with mock.patch.object(self.engine, "discover_schedules") as reads, mock.patch.object(self.engine, "set_schedules_batch") as writes, mock.patch.object(self.engine, "handle_active_cleaning") as dock:
                self.engine.maintain_pause()
                saved = self.fixture.paths["STATE_FILE"].read_bytes()
                self.now = 100000
                self.engine.maintain_pause()
                reads.assert_not_called()
                writes.assert_not_called()
                dock.assert_not_called()
                self.assertEqual(saved, self.fixture.paths["STATE_FILE"].read_bytes())
            self.assertEqual(self.engine.load_state()["operation"]["phase"], "needs-attention")
            self.assertTrue(self.fixture.paths["BACKUP_SNAPSHOT_FILE"].exists())

    def test_completed_window_closes_without_another_homebridge_read(self):
        self.start(False)
        for when in range(1000, 1180, 60):
            self.tick(when, first=True)
        self.now = 1180
        with mock.patch.object(self.engine, "api_request") as api:
            self.engine.maintain_pause()
            self.now = 100000
            self.engine.maintain_pause()
            api.assert_not_called()
        self.assertEqual(self.engine.load_state()["operation"]["phase"], "complete")
        self.assertFalse(self.engine.display_pause_state(self.engine.load_state()))

    def test_legacy_completed_and_unfinished_journals_get_no_new_timer_budget(self):
        for phase in ("complete", "settling", "planning"):
            snapshot = self.start(False)
            operation = snapshot["reconciliation"]
            operation.pop("window")
            operation["phase"] = phase
            operation["auditOnly"] = phase == "complete"
            self.engine.save_reconciliation(snapshot)
            with mock.patch.object(self.engine, "api_request") as api:
                self.engine.maintain_pause()
                self.engine.maintain_pause()
                api.assert_not_called()
            self.assertEqual(self.snapshot()["reconciliation"]["phase"],
                             "complete" if phase == "complete" else "needs-attention")
            self.assertTrue(self.snapshot()["reconciliation"]["window"]["closed"])

    def test_expired_initial_discovery_is_not_restarted_by_timer(self):
        self.engine.persist_state(False, "initial")
        with mock.patch.object(self.engine, "discover_schedules_with_recovery", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.activate_pause()
        original = self.engine.load_state()["pendingActivationWindow"]
        self.now = 1180
        with mock.patch.object(self.engine, "activate_pause") as resume:
            self.engine.maintain_pause()
            self.now = 2000
            self.engine.maintain_pause()
            resume.assert_not_called()
        state = self.engine.load_state()
        self.assertTrue(state["pendingActivationWindow"]["closed"])
        self.assertEqual(state["pendingActivationWindow"]["endsAt"], original["endsAt"])
        self.assertFalse(self.engine.display_pause_state(state))
        with mock.patch.object(self.engine, "discover_schedules_with_recovery", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.activate_pause()
        self.assertEqual(self.engine.load_state()["pendingActivationWindow"]["endsAt"], 2180)

    def test_resumed_initial_discovery_keeps_original_deadline(self):
        self.engine.persist_state(False, "initial")
        with mock.patch.object(self.engine, "discover_schedules_with_recovery", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.activate_pause()
        self.now = 1060
        with mock.patch.object(self.engine, "discover_schedules", return_value=[fixtures.schedule("1", False)]), mock.patch.object(self.engine, "handle_active_cleaning"):
            self.engine.maintain_pause()
        self.assertEqual(self.snapshot()["reconciliation"]["window"]["endsAt"], 1180)

    def test_restore_planning_does_not_renew_deadline_after_outage(self):
        self.start()
        with mock.patch.object(self.engine, "discover_schedules_with_recovery", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.deactivate_pause()
        self.now = 1060
        with mock.patch.object(self.engine, "set_schedules_batch", return_value=[]):
            self.tick(1060)
        self.assertEqual(self.snapshot()["reconciliation"]["window"]["endsAt"], 1180)

    def test_slow_discovery_crossing_deadline_cannot_start_retry_or_dock(self):
        self.start()
        def slow_read():
            self.now = 1181
            return [fixtures.schedule("1", True), fixtures.schedule("2", False)]
        with mock.patch.object(self.engine, "discover_schedules", side_effect=slow_read), mock.patch.object(self.engine, "set_schedules_batch") as writes, mock.patch.object(self.engine, "handle_active_cleaning") as dock:
            with self.assertRaises(self.engine.VerificationWindowExpired):
                self.engine.maintain_pause()
            writes.assert_not_called()
            dock.assert_not_called()

    def test_http_guard_blocks_requests_after_deadline(self):
        self.start()
        self.now = 1180
        with mock.patch.object(self.engine.urllib.request, "urlopen") as network:
            for call in (lambda: self.engine.api_request("GET", "/api/accessories"),
                         lambda: self.engine.api_request_with_token("PUT", "/api/accessories/1", {}, "token", 30),
                         self.engine.refresh_access_token):
                with self.assertRaises(self.engine.VerificationWindowExpired):
                    call()
            network.assert_not_called()

    def test_explicit_retry_reopens_failed_restore_with_saved_targets(self):
        self.start(False)
        self.now = 1180
        self.engine.maintain_pause()
        self.now = 2000
        with mock.patch.object(self.engine, "discover_schedules", return_value=[fixtures.schedule("1", False), fixtures.schedule("2", False)]), mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.engine.deactivate_pause()
            self.assertEqual(writes.call_args.args[0][0][1], True)
        operation = self.snapshot()["reconciliation"]
        self.assertEqual(operation["targets"], {"1": True})
        self.assertEqual(operation["window"]["endsAt"], 2180)

    def test_matching_write_result_can_settle_before_five_minute_cache_expiry(self):
        self.start()
        for when in (1000, 1060, 1120):
            self.tick(when)
        self.assertEqual(self.snapshot()["reconciliation"]["phase"], "complete")
        self.assertLess(self.now - 1000, 300)
        self.assertEqual(self.snapshot()["reconciliation"]["window"]["endsAt"], 1180)

    def test_prior_development_window_is_capped_without_getting_a_new_budget(self):
        snapshot = self.start()
        snapshot["reconciliation"]["window"]["endsAt"] = 1600
        self.engine.persist_active_snapshot(snapshot)
        self.now = 1180
        with mock.patch.object(self.engine, "api_request") as api:
            self.engine.maintain_pause()
            api.assert_not_called()
        self.assertTrue(self.snapshot()["reconciliation"]["window"]["closed"])

    def test_clock_rollback_closes_window_instead_of_extending_it(self):
        self.start()
        self.now = 999
        with mock.patch.object(self.engine, "discover_schedules") as reads:
            self.engine.maintain_pause()
            reads.assert_not_called()
        self.assertTrue(self.snapshot()["reconciliation"]["window"]["closed"])

    def test_invalid_window_fails_closed_without_network(self):
        for value in ({"startedAt": 1000, "endsAt": 999999, "closed": False},
                      {"startedAt": 1000, "endsAt": float("nan"), "closed": False}):
            snapshot = self.start()
            snapshot["reconciliation"]["window"] = value
            self.engine.persist_active_snapshot(snapshot)
            with mock.patch.object(self.engine, "api_request") as api:
                with self.assertRaisesRegex(RuntimeError, "No valid"):
                    self.engine.maintain_pause()
                api.assert_not_called()

    def test_idle_tick_without_pause_history_never_calls_homebridge(self):
        self.engine.persist_state(False, "initial")
        with mock.patch.object(self.engine, "api_request") as api:
            self.engine.maintain_pause()
            api.assert_not_called()

    def test_repeated_open_request_and_timer_restart_keep_deadline(self):
        self.start()
        self.now = 1120
        with mock.patch.object(self.engine, "discover_schedules", return_value=[fixtures.schedule("1", False), fixtures.schedule("2", False)]):
            self.engine.activate_pause()
            self.engine.REQUEST_WINDOW = None  # fresh process has no in-memory budget
            self.now = 1150
            self.engine.maintain_pause()
        self.assertEqual(self.snapshot()["reconciliation"]["window"]["endsAt"], 1180)

    def test_opposite_request_gets_its_own_deadline(self):
        self.start()
        self.now = 1540
        with mock.patch.object(self.engine, "discover_schedules", return_value=[fixtures.schedule("1", True), fixtures.schedule("2", False)]):
            self.engine.deactivate_pause()
        self.assertEqual(self.snapshot()["reconciliation"]["window"]["endsAt"], 1720)

    def test_expired_activation_summary_is_consistent_for_individual_and_all(self):
        state = self.engine.load_state()
        state["pauseActive"] = False
        state["pendingActivation"] = True
        state["pendingActivationWindow"] = {"startedAt": 1000, "endsAt": 1600, "closed": True}
        self.assertFalse(self.engine.display_pause_state(state))
        self.assertFalse(aggregate_fixtures.MODULE.display_state(state))

    def test_expired_restore_planning_is_replanned_on_explicit_retry(self):
        self.start()
        with mock.patch.object(self.engine, "discover_schedules_with_recovery", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.engine.deactivate_pause()
        self.now = 1180
        self.engine.maintain_pause()
        self.assertTrue(self.engine.load_state()["operation"]["planningRequired"])
        self.now = 2000
        with mock.patch.object(self.engine, "discover_schedules", return_value=[fixtures.schedule("1", False), fixtures.schedule("2", False)]), mock.patch.object(self.engine, "set_schedules_batch", return_value=[]) as writes:
            self.engine.deactivate_pause()
            self.assertEqual(writes.call_args.args[0][0][1], True)
        self.assertEqual(self.snapshot()["reconciliation"]["targets"], {"1": True, "2": False})
        self.assertFalse(self.snapshot()["reconciliation"]["planningRequired"])


class AggregateMaintenanceTests(unittest.TestCase):
    def test_failure_for_one_vacuum_does_not_skip_the_other(self):
        engine = aggregate_fixtures.MODULE
        with mock.patch.object(engine, "ROOT", "/test-runtime"), mock.patch.object(engine, "VACUUMS", ({"id": "one"}, {"id": "two"})), mock.patch.object(engine.subprocess, "run", side_effect=[mock.Mock(returncode=1), mock.Mock(returncode=0)]) as run:
            self.assertEqual(engine.maintain_all(), 1)
            self.assertEqual(run.call_count, 2)
            self.assertTrue(all(call.args[0][-1] == "maintain" for call in run.call_args_list))
