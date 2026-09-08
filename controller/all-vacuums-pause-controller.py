#!/usr/bin/env python3

import fcntl
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pause_until_tomorrow import read_enabled
from vacuum_registry import load_registry, orchestrator_vacuums

REGISTRY = None
ROOT = None
LOCK_FILE = None
OPERATION_LOG_FILE = None
VACUUMS = ()


def configure_runtime():
    global REGISTRY, ROOT, LOCK_FILE, OPERATION_LOG_FILE, VACUUMS
    REGISTRY = load_registry()
    ROOT = str(REGISTRY["root"])
    LOCK_FILE = f"{ROOT}/controller/all-vacuums-pause.lock"
    OPERATION_LOG_FILE = f"{ROOT}/controller/all-vacuums-pause-operation.log"
    VACUUMS = orchestrator_vacuums(REGISTRY)


@contextmanager
def operation_lock():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def load_vacuum_state(vacuum):
    with open(vacuum["stateFile"], encoding="utf-8") as handle:
        state = json.load(handle)

    if not isinstance(state, dict):
        raise RuntimeError(f"{vacuum['displayName']}: state is not an object")
    if state.get("version") != 1 or state.get("vacuumId") != vacuum["id"]:
        raise RuntimeError(
            f"{vacuum['displayName']}: state schema or vacuum ID is invalid"
        )
    if not isinstance(state.get("pauseActive"), bool):
        raise RuntimeError(f"{vacuum['displayName']}: pauseActive is invalid")
    if not isinstance(state.get("sessionId"), str) or not state["sessionId"]:
        raise RuntimeError(f"{vacuum['displayName']}: sessionId is invalid")

    state["transactionStatus"] = "inactive"
    if state["pauseActive"]:
        state["transactionStatus"] = validate_active_snapshot(vacuum, state)

    return state


def validate_active_snapshot(vacuum, state):
    with open(vacuum["snapshotFile"], encoding="utf-8") as handle:
        snapshot = json.load(handle)

    label = vacuum["displayName"]
    if not isinstance(snapshot, dict) or snapshot.get("version") != 3:
        raise RuntimeError(f"{label}: active snapshot schema is invalid")
    if snapshot.get("vacuumId") != vacuum["id"]:
        raise RuntimeError(f"{label}: active snapshot vacuum ID is invalid")
    if snapshot.get("sessionId") != state["sessionId"]:
        raise RuntimeError(f"{label}: active snapshot session does not match state")

    schedules = snapshot.get("schedules")
    if not isinstance(schedules, list) or snapshot.get("scheduleCount") != len(
        schedules
    ):
        raise RuntimeError(f"{label}: active snapshot schedule list is invalid")
    if not schedules:
        raise RuntimeError(f"{label}: active snapshot has no schedules")
    schedule_ids = set()
    for schedule in schedules:
        if not isinstance(schedule, dict):
            raise RuntimeError(f"{label}: active snapshot schedule is invalid")
        if not isinstance(schedule.get("uniqueId"), str) or not schedule["uniqueId"]:
            raise RuntimeError(f"{label}: active snapshot schedule ID is invalid")
        if schedule["uniqueId"] in schedule_ids:
            raise RuntimeError(f"{label}: active snapshot schedule ID is duplicated")
        schedule_ids.add(schedule["uniqueId"])
        if not isinstance(schedule.get("prePauseOn"), bool):
            raise RuntimeError(f"{label}: active snapshot prePauseOn is invalid")
        if not isinstance(schedule.get("pauseEstablishedOn"), bool):
            raise RuntimeError(
                f"{label}: active snapshot pauseEstablishedOn is invalid"
            )

    action = snapshot.get("vacuumAction")
    valid_actions = {
        "pending-schedule-disable",
        "return-to-dock-requested",
        "return-to-dock-acknowledged",
        "not-required-idle-docked",
        "not-required-not-cleaning",
        "transition-confirmed",
        "inconsistent-sensors",
        "discovery-failed",
        "write-failed",
        "transition-timeout",
    }
    if not isinstance(action, dict) or action.get("result") not in valid_actions:
        raise RuntimeError(f"{label}: active snapshot vacuum action is invalid")

    schedules_complete = all(
        schedule["pauseEstablishedOn"] is False for schedule in schedules
    )
    successful_actions = {
        "return-to-dock-acknowledged",
        "not-required-idle-docked",
        "not-required-not-cleaning",
        "transition-confirmed",
    }
    if schedules_complete and action["result"] in successful_actions:
        return "complete"
    if action["result"] == "pending-schedule-disable":
        return "in-progress"
    return "recovery-required"


def read_states():
    return {
        vacuum["id"]: load_vacuum_state(vacuum)["pauseActive"]
        for vacuum in VACUUMS
    }


def read_state_details():
    return {vacuum["id"]: load_vacuum_state(vacuum) for vacuum in VACUUMS}


def combined_state(states):
    return any(states.values())


def run_vacuum_command(vacuum, command_key):
    command = vacuum[command_key]
    if isinstance(command, str):
        command = [command]
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
    )
    return vacuum, result


def print_result(vacuum, result):
    label = vacuum["displayName"]
    print(f"===== {label.upper()} RESULT (exit {result.returncode}) =====")
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(f"----- {label} stderr -----")
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")


def dispatch_set_all(desired):
    """Start a detached aggregate operation and return to HomeKit promptly."""
    command = "on" if desired else "off"
    descriptor = os.open(
        OPERATION_LOG_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), command],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    print(
        f"Pause All {'ON' if desired else 'OFF'} accepted; "
        f"operation continues as PID {process.pid}."
    )
    print(f"Operation log: {OPERATION_LOG_FILE}")
    return 0


def set_all(desired):
    action = "ACTIVATE" if desired else "DEACTIVATE"
    command_key = "onCommand" if desired else "offCommand"
    print(f"===== PAUSE ALL VACUUMS {action} =====")
    print()

    initial_details = read_state_details()
    initial = {
        vacuum_id: state["pauseActive"]
        for vacuum_id, state in initial_details.items()
    }
    targets = []
    for vacuum in VACUUMS:
        state = initial_details[vacuum["id"]]
        if state["pauseActive"] != desired:
            targets.append(vacuum)
        elif desired and state["transactionStatus"] != "complete":
            targets.append(vacuum)

    for vacuum in VACUUMS:
        current = initial[vacuum["id"]]
        detail = initial_details[vacuum["id"]]["transactionStatus"]
        if desired and current and detail != "complete":
            print(
                f"{vacuum['displayName']}: ACTIVE ({detail}); "
                "reconciling the existing transaction."
            )
        elif current == desired:
            print(
                f"{vacuum['displayName']}: already "
                f"{'ACTIVE' if desired else 'INACTIVE'}"
                f"{f' ({detail})' if desired else ''}; no command required."
            )
        else:
            print(
                f"{vacuum['displayName']}: "
                f"{'activating' if desired else 'deactivating'}."
            )

    results = []
    if targets:
        print()
        print(f"Submitting {len(targets)} per-vacuum operations concurrently...")
        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            futures = {
                executor.submit(run_vacuum_command, vacuum, command_key): vacuum
                for vacuum in targets
            }
            for future in as_completed(futures):
                vacuum = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    synthetic = subprocess.CompletedProcess(
                        vacuum[command_key],
                        1,
                        "",
                        str(exc),
                    )
                    results.append((vacuum, synthetic))

    for vacuum, result in sorted(
        results,
        key=lambda item: item[0]["displayName"],
    ):
        print()
        print_result(vacuum, result)

    final_details = read_state_details()
    final = {
        vacuum_id: state["pauseActive"]
        for vacuum_id, state in final_details.items()
    }
    failures = [
        f"{vacuum['displayName']}: command exited {result.returncode}"
        for vacuum, result in results
        if result.returncode != 0
    ]
    failures.extend(
        f"{vacuum['displayName']}: expected "
        f"{'ACTIVE' if desired else 'INACTIVE'}, found "
        f"{'ACTIVE' if final[vacuum['id']] else 'INACTIVE'}"
        for vacuum in VACUUMS
        if final[vacuum["id"]] != desired
    )

    print()
    print("===== PAUSE ALL FINAL STATE =====")
    for vacuum in VACUUMS:
        is_active = final[vacuum["id"]]
        detail = final_details[vacuum["id"]]["transactionStatus"]
        suffix = f" ({detail})" if is_active else ""
        print(
            f"{vacuum['displayName']}: "
            f"{'ACTIVE' if is_active else 'INACTIVE'}{suffix}"
        )

    if failures:
        print()
        print("RESULT: FAIL")
        print("Pause All is partially applied or could not be verified.")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print()
    print("RESULT: PASS")
    print(f"All vacuums are {'ACTIVE' if desired else 'INACTIVE'}.")
    return 0


def expire_pauses():
    print("===== AUTOMATIC PAUSE EXPIRATION =====")
    print()
    if not read_enabled():
        print("Pause Until Tomorrow is DISABLED; no pause was changed.")
        return 0

    print("Pause Until Tomorrow is ENABLED.")
    return set_all(False)


def main():
    if len(sys.argv) != 2 or sys.argv[1].lower() not in (
        "on",
        "off",
        "dispatch-on",
        "dispatch-off",
        "state",
        "expire",
    ):
        print(
            "Usage: all-vacuums-pause-controller.py "
            "on|off|dispatch-on|dispatch-off|state|expire",
            file=sys.stderr,
        )
        return 2

    command = sys.argv[1].lower()
    try:
        if not VACUUMS:
            configure_runtime()
        if command == "dispatch-on":
            return dispatch_set_all(True)
        if command == "dispatch-off":
            return dispatch_set_all(False)
        if command == "state":
            print("true" if combined_state(read_states()) else "false")
            return 0

        with operation_lock():
            if command == "expire":
                return expire_pauses()
            return set_all(command == "on")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
