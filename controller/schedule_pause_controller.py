#!/usr/bin/env python3

import json
import os
import sys
import fcntl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8581"
DEFAULT_TOKEN_FILE = (
    "/var/lib/homebridge/roborockPauseSchedules/homebridge-access-token"
)
DEFAULT_AUTO_AUTH_TOKEN_FILE = (
    "/var/lib/homebridge/roborockPauseSchedules/controller/homebridge-access-token"
)
DEFAULT_AUTO_AUTH_CREDENTIALS_FILE = (
    "/var/lib/homebridge/roborockPauseSchedules/controller/"
    "homebridge-api-credentials.json"
)

BASE_URL = DEFAULT_BASE_URL
TOKEN_FILE = DEFAULT_TOKEN_FILE
AUTO_AUTH_TOKEN_FILE = DEFAULT_AUTO_AUTH_TOKEN_FILE
AUTO_AUTH_CREDENTIALS_FILE = DEFAULT_AUTO_AUTH_CREDENTIALS_FILE


@dataclass(frozen=True)
class VacuumConfig:
    vacuum_id: str
    display_name: str
    schedule_name_prefix: str
    cleaning_service_name: str
    docked_service_name: str
    return_to_dock_service_name: str
    state_file: str
    snapshot_file: str
    backup_snapshot_file: str
    lock_file: str


def configure_homebridge(
    base_url,
    legacy_token_file,
    token_file,
    credentials_file,
):
    """Configure Homebridge API and authentication locations."""
    global BASE_URL, TOKEN_FILE, AUTO_AUTH_TOKEN_FILE, AUTO_AUTH_CREDENTIALS_FILE

    if not isinstance(base_url, str) or not base_url.startswith(
        ("http://", "https://")
    ):
        raise ValueError("Homebridge base URL must be an HTTP(S) URL")
    paths = (legacy_token_file, token_file, credentials_file)
    if any(not isinstance(path, str) or not os.path.isabs(path) for path in paths):
        raise ValueError("Homebridge authentication paths must be absolute")

    BASE_URL = base_url.rstrip("/")
    TOKEN_FILE = legacy_token_file
    AUTO_AUTH_TOKEN_FILE = token_file
    AUTO_AUTH_CREDENTIALS_FILE = credentials_file


def configure(config):
    """Configure the reusable controller for exactly one vacuum."""
    global VACUUM_ID, VACUUM_DISPLAY_NAME, SCHEDULE_NAME_PREFIX
    global CLEANING_SERVICE_NAME, DOCKED_SERVICE_NAME, RETURN_TO_DOCK_SERVICE_NAME
    global STATE_FILE, SNAPSHOT_FILE, BACKUP_SNAPSHOT_FILE, LOCK_FILE

    if not isinstance(config, VacuumConfig):
        raise TypeError("config must be a VacuumConfig")
    required = (
        config.vacuum_id,
        config.display_name,
        config.schedule_name_prefix,
        config.cleaning_service_name,
        config.docked_service_name,
        config.return_to_dock_service_name,
        config.state_file,
        config.snapshot_file,
        config.backup_snapshot_file,
        config.lock_file,
    )
    if any(not isinstance(value, str) or not value for value in required):
        raise ValueError("vacuum configuration values must be non-empty strings")

    VACUUM_ID = config.vacuum_id
    VACUUM_DISPLAY_NAME = config.display_name
    SCHEDULE_NAME_PREFIX = config.schedule_name_prefix
    CLEANING_SERVICE_NAME = config.cleaning_service_name
    DOCKED_SERVICE_NAME = config.docked_service_name
    RETURN_TO_DOCK_SERVICE_NAME = config.return_to_dock_service_name
    STATE_FILE = config.state_file
    SNAPSHOT_FILE = config.snapshot_file
    BACKUP_SNAPSHOT_FILE = config.backup_snapshot_file
    LOCK_FILE = config.lock_file


API_READ_TIMEOUT_SECONDS = 15
SCHEDULE_WRITE_TIMEOUT_SECONDS = 30
SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS = (1, 2, 4, 8, 10)
SUPPORTED_SNAPSHOT_VERSIONS = (1, 2, 3)
TOKEN_REFRESH_LOCK = Lock()

VACUUM_ACTION_RESULTS = {
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


@contextmanager
def operation_lock():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_state():
    state = load_json(STATE_FILE)
    if state.get("version") != 1 or state.get("vacuumId") != VACUUM_ID:
        raise RuntimeError("Pause state schema or vacuum ID is invalid")
    if not isinstance(state.get("pauseActive"), bool):
        raise RuntimeError("Pause state pauseActive is invalid")
    if not isinstance(state.get("sessionId"), str) or not state["sessionId"]:
        raise RuntimeError("Pause state sessionId is invalid")
    return state


def validate_snapshot(snapshot, expected_session):
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("version") not in SUPPORTED_SNAPSHOT_VERSIONS
    ):
        raise RuntimeError("Snapshot schema version is invalid")
    if snapshot.get("vacuumId") != VACUUM_ID:
        raise RuntimeError(
            f"Snapshot vacuum ID does not match {VACUUM_DISPLAY_NAME}"
        )
    if snapshot.get("sessionId") != expected_session:
        raise RuntimeError("Snapshot session ID does not match active pause state")
    schedules = snapshot.get("schedules")
    if not isinstance(schedules, list) or snapshot.get("scheduleCount") != len(
        schedules
    ):
        raise RuntimeError("Snapshot schedule list/count is invalid")
    seen = set()
    for schedule in schedules:
        unique_id = schedule.get("uniqueId")
        if not unique_id or unique_id in seen:
            raise RuntimeError("Snapshot contains a missing or duplicate uniqueId")
        seen.add(unique_id)
        if "prePauseOn" not in schedule:
            schedule["prePauseOn"] = bool(schedule.get("on"))
        if "pauseEstablishedOn" not in schedule:
            schedule["pauseEstablishedOn"] = False
        if not isinstance(schedule["prePauseOn"], bool):
            raise RuntimeError("Snapshot prePauseOn is invalid")
        if not isinstance(schedule["pauseEstablishedOn"], bool):
            raise RuntimeError("Snapshot pauseEstablishedOn is invalid")
    if snapshot["version"] == 3:
        vacuum_action = snapshot.get("vacuumAction")
        if not isinstance(vacuum_action, dict):
            raise RuntimeError("Snapshot vacuumAction is invalid")
        if vacuum_action.get("result") not in VACUUM_ACTION_RESULTS:
            raise RuntimeError("Snapshot vacuumAction result is invalid")
        if (
            not isinstance(vacuum_action.get("updatedAt"), str)
            or not vacuum_action["updatedAt"]
        ):
            raise RuntimeError("Snapshot vacuumAction updatedAt is invalid")
        for field in ("cleaningObserved", "dockedObserved"):
            if field in vacuum_action and not isinstance(vacuum_action[field], bool):
                raise RuntimeError(f"Snapshot vacuumAction {field} is invalid")
        for field in (
            "action",
            "actionServiceName",
            "actionUniqueId",
            "requestedAt",
            "acknowledgedAt",
            "transitionObservedAt",
            "error",
        ):
            if field in vacuum_action and not isinstance(vacuum_action[field], str):
                raise RuntimeError(f"Snapshot vacuumAction {field} is invalid")
    return snapshot


def load_active_snapshot(state):
    errors = []
    for label, path in (("primary", SNAPSHOT_FILE), ("backup", BACKUP_SNAPSHOT_FILE)):
        try:
            snapshot = validate_snapshot(load_json(path), state["sessionId"])
            if label == "backup":
                print("WARNING: Using matching valid backup snapshot.")
            return snapshot
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{label}: {exc}")
    raise RuntimeError("No valid matching active snapshot: " + "; ".join(errors))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def session_id():
    return datetime.now(timezone.utc).strftime(f"{VACUUM_ID}-%Y%m%dT%H%M%SZ")


def load_token():
    token_paths = (AUTO_AUTH_TOKEN_FILE, TOKEN_FILE)
    errors = []
    for path in token_paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
            errors.append(f"{path}: empty")
        except OSError as exc:
            errors.append(f"{path}: {exc}")

    raise RuntimeError("No usable Homebridge access token: " + "; ".join(errors))


def load_auto_auth_credentials():
    try:
        credentials = load_json(AUTO_AUTH_CREDENTIALS_FILE)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Automatic Homebridge authentication is not configured"
        ) from exc

    if not isinstance(credentials, dict):
        raise RuntimeError("Automatic Homebridge credentials are invalid")
    username = credentials.get("username")
    password = credentials.get("password")
    if not isinstance(username, str) or not username:
        raise RuntimeError("Automatic Homebridge username is invalid")
    if not isinstance(password, str) or not password:
        raise RuntimeError("Automatic Homebridge password is invalid")
    return username, password


def write_token_atomic(token):
    if not isinstance(token, str) or not token:
        raise RuntimeError("Homebridge login returned an invalid access token")
    directory = os.path.dirname(AUTO_AUTH_TOKEN_FILE)
    os.makedirs(directory, exist_ok=True)
    temporary = f"{AUTO_AUTH_TOKEN_FILE}.tmp.{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, AUTO_AUTH_TOKEN_FILE)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def refresh_access_token():
    username, password = load_auto_auth_credentials()
    data = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + "/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=API_READ_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Automatic Homebridge authentication failed with HTTP {exc.code}"
        ) from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError("Automatic Homebridge authentication failed") from exc

    token = payload.get("access_token") if isinstance(payload, dict) else None
    write_token_atomic(token)
    return token


def refresh_access_token_if_stale(failed_token=None):
    with TOKEN_REFRESH_LOCK:
        if failed_token is not None:
            try:
                current_token = load_token()
            except RuntimeError:
                current_token = None
            if current_token and current_token != failed_token:
                return current_token
        return refresh_access_token()


def api_request(method, path, body=None, timeout=API_READ_TIMEOUT_SECONDS):
    try:
        token = load_token()
    except RuntimeError:
        token = refresh_access_token_if_stale()

    try:
        return api_request_with_token(method, path, body, token, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {method} {path}: {raw[:500]}")

    token = refresh_access_token_if_stale(token)
    try:
        return api_request_with_token(method, path, body, token, timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {method} {path}: {raw[:500]}")


def api_request_with_token(method, path, body, token, timeout):

    url = BASE_URL + path

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    data = None

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw) if raw else None


def discover_schedules():
    accessories = get_accessories()

    schedules = []

    for accessory in accessories:
        name = accessory.get("serviceName") or ""

        if not name.startswith(SCHEDULE_NAME_PREFIX):
            continue

        on_value = None

        for characteristic in accessory.get("serviceCharacteristics") or []:
            if characteristic.get("type") == "On":
                on_value = bool(characteristic.get("value"))
                break

        if on_value is None:
            continue

        schedules.append(
            {
                "serviceName": name,
                "uniqueId": accessory.get("uniqueId"),
                "aid": accessory.get("aid"),
                "iid": accessory.get("iid"),
                "on": on_value,
            }
        )

    schedules.sort(key=lambda x: x["serviceName"])

    return schedules


def get_accessories():
    status, accessories = api_request("GET", "/api/accessories")
    if status != 200:
        raise RuntimeError(f"Unexpected Homebridge response status: {status}")
    if not isinstance(accessories, list):
        raise RuntimeError("Homebridge accessory response is not a list")
    return accessories


def find_accessory_service(
    accessories, service_name, characteristic_type, writable=False
):
    matches = [
        accessory
        for accessory in accessories
        if accessory.get("serviceName") == service_name
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"{service_name}: expected exactly one service, found {len(matches)}"
        )

    accessory = matches[0]
    unique_id = accessory.get("uniqueId")
    if not isinstance(unique_id, str) or not unique_id:
        raise RuntimeError(f"{service_name}: uniqueId is missing")

    characteristics = [
        characteristic
        for characteristic in accessory.get("serviceCharacteristics") or []
        if characteristic.get("type") == characteristic_type
    ]
    if len(characteristics) != 1:
        raise RuntimeError(
            f"{service_name}: expected exactly one {characteristic_type} "
            f"characteristic, found {len(characteristics)}"
        )

    characteristic = characteristics[0]
    permissions = characteristic.get("perms") or []
    if "pr" not in permissions:
        raise RuntimeError(f"{service_name}: {characteristic_type} is not readable")
    if writable and "pw" not in permissions:
        raise RuntimeError(f"{service_name}: {characteristic_type} is not writable")

    return {
        "serviceName": service_name,
        "uniqueId": unique_id,
        "characteristic": characteristic,
    }


def contact_sensor_is_detected(service):
    value = service["characteristic"].get("value")
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise RuntimeError(
            f"{service['serviceName']}: ContactSensorState must be 0 or 1"
        )
    return value == 0


def discover_vacuum_state(require_action=False):
    accessories = get_accessories()
    cleaning_service = find_accessory_service(
        accessories,
        CLEANING_SERVICE_NAME,
        "ContactSensorState",
    )
    docked_service = find_accessory_service(
        accessories,
        DOCKED_SERVICE_NAME,
        "ContactSensorState",
    )
    state = {
        "cleaning": contact_sensor_is_detected(cleaning_service),
        "docked": contact_sensor_is_detected(docked_service),
    }
    if require_action:
        state["returnToDock"] = find_accessory_service(
            accessories,
            RETURN_TO_DOCK_SERVICE_NAME,
            "On",
            writable=True,
        )
    return state


def trigger_return_to_dock(service):
    status, _ = api_request(
        "PUT",
        f"/api/accessories/{service['uniqueId']}",
        {"characteristicType": "On", "value": True},
    )
    if status != 200:
        raise RuntimeError(f"{service['serviceName']}: unexpected PUT status {status}")


def update_vacuum_action(snapshot, result, **fields):
    action = snapshot["vacuumAction"]
    action.update(fields)
    action["result"] = result
    action["updatedAt"] = utc_now()
    persist_active_snapshot(snapshot)


def handle_active_cleaning(snapshot):
    try:
        state = discover_vacuum_state(require_action=True)
    except Exception as exc:
        update_vacuum_action(snapshot, "discovery-failed", error=str(exc))
        raise

    observed = {
        "cleaningObserved": state["cleaning"],
        "dockedObserved": state["docked"],
    }

    print()
    print(f"===== {VACUUM_DISPLAY_NAME.upper()} ACTIVE-CLEANING CHECK =====")
    print(f"Cleaning: {'YES' if state['cleaning'] else 'NO'}")
    print(f"Docked:   {'YES' if state['docked'] else 'NO'}")

    if state["cleaning"] and state["docked"]:
        message = "Cleaning and Docked sensors are simultaneously active"
        update_vacuum_action(
            snapshot,
            "inconsistent-sensors",
            error=message,
            **observed,
        )
        raise RuntimeError(message)

    if state["docked"]:
        update_vacuum_action(
            snapshot,
            "not-required-idle-docked",
            **observed,
        )
        print("Return to Dock: not required; vacuum is already docked.")
        return

    if not state["cleaning"]:
        update_vacuum_action(
            snapshot,
            "not-required-not-cleaning",
            **observed,
        )
        print("Return to Dock: not required; vacuum is not cleaning.")
        return

    action = state["returnToDock"]
    requested_at = utc_now()
    update_vacuum_action(
        snapshot,
        "return-to-dock-requested",
        action="returnToDock",
        actionServiceName=action["serviceName"],
        actionUniqueId=action["uniqueId"],
        requestedAt=requested_at,
        cleaningObserved=True,
        dockedObserved=False,
    )
    try:
        trigger_return_to_dock(action)
    except Exception as exc:
        update_vacuum_action(snapshot, "write-failed", error=str(exc), **observed)
        raise

    update_vacuum_action(
        snapshot,
        "return-to-dock-acknowledged",
        acknowledgedAt=utc_now(),
        **observed,
    )
    print("Return to Dock: command acknowledged; arrival is not monitored.")


def write_json_atomic(path, data):
    directory = os.path.dirname(path)
    basename = os.path.basename(path)
    temporary = os.path.join(directory, f".{basename}.tmp")

    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            descriptor = -1
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def persist_snapshot(snapshot):
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)

            if (
                isinstance(existing, dict)
                and existing.get("vacuumId") == VACUUM_ID
                and isinstance(existing.get("schedules"), list)
                and existing.get("scheduleCount") == len(existing["schedules"])
            ):
                write_json_atomic(BACKUP_SNAPSHOT_FILE, existing)
                print("Previous valid snapshot copied to backup.")

        except Exception as e:
            print(f"WARNING: Existing snapshot could not be validated: {e}")
            print("WARNING: Existing snapshot will not be copied to backup.")

    write_json_atomic(SNAPSHOT_FILE, snapshot)
    print(f"Snapshot persisted: {SNAPSHOT_FILE}")


def persist_active_snapshot(snapshot):
    write_json_atomic(SNAPSHOT_FILE, snapshot)
    write_json_atomic(BACKUP_SNAPSHOT_FILE, snapshot)


def persist_state(active, sid):
    state = {
        "version": 1,
        "vacuumId": VACUUM_ID,
        "pauseActive": active,
        "sessionId": sid,
        "updatedAt": utc_now(),
    }

    write_json_atomic(STATE_FILE, state)

    print(f"Pause state persisted: {'ACTIVE' if active else 'INACTIVE'}")


def set_schedule(schedule, desired):
    name = schedule["serviceName"]
    unique_id = schedule["uniqueId"]

    status, _ = api_request(
        "PUT",
        f"/api/accessories/{unique_id}",
        {
            "characteristicType": "On",
            "value": desired,
        },
        timeout=SCHEDULE_WRITE_TIMEOUT_SECONDS,
    )

    if status != 200:
        raise RuntimeError(f"{name}: unexpected PUT status {status}")

    return desired


def set_schedules_batch(changes):
    """Submit related switch writes together so the plugin can batch them."""
    if not changes:
        return []

    failures = []
    with ThreadPoolExecutor(max_workers=len(changes)) as executor:
        futures = {
            executor.submit(set_schedule, schedule, desired): schedule
            for schedule, desired in changes
        }
        for future in as_completed(futures):
            schedule = futures[future]
            try:
                future.result()
            except Exception as exc:
                failures.append(f"{schedule['serviceName']}: {exc}")

    return failures


def discover_schedules_with_recovery(context):
    """Retry bounded transient reads while the plugin recovers its cloud session."""
    last_error = None
    attempts = len(SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS) + 1
    for attempt in range(attempts):
        try:
            return discover_schedules()
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            delay = SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS[attempt]
            print(
                f"{context}: transient schedule read failed; the Roborock "
                f"plugin may be recovering ({exc}). Retrying in {delay} "
                f"second{'s' if delay != 1 else ''}..."
            )
            time.sleep(delay)
    raise RuntimeError(
        f"{context}: schedule read recovery exhausted after {attempts} "
        f"attempts: {last_error}"
    ) from last_error


def verify_schedule_changes(changes):
    for attempt in range(len(SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS) + 1):
        try:
            live = discover_schedules()
        except Exception as exc:
            if attempt == len(SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS):
                raise RuntimeError(
                    "Schedule verification recovery exhausted: " + str(exc)
                ) from exc
            delay = SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS[attempt]
            print(
                "Schedule verification read failed; the Roborock plugin may "
                f"be recovering ({exc}). Retrying in {delay} "
                f"second{'s' if delay != 1 else ''}..."
            )
            time.sleep(delay)
            continue
        live_by_id = {schedule["uniqueId"]: schedule for schedule in live}
        failures = []
        for schedule, desired in changes:
            current = live_by_id.get(schedule["uniqueId"])
            if current is None:
                failures.append(
                    f"{schedule['serviceName']}: missing from final discovery"
                )
            elif current["on"] != desired:
                failures.append(
                    f"{schedule['serviceName']}: expected "
                    f"{'ON' if desired else 'OFF'}, got "
                    f"{'ON' if current['on'] else 'OFF'}"
                )

        if not failures or attempt == len(SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS):
            return live, failures

        delay = SCHEDULE_VERIFICATION_RETRY_DELAYS_SECONDS[attempt]
        print(
            "Schedule verification is not settled; "
            f"retrying in {delay} second{'s' if delay != 1 else ''}..."
        )
        time.sleep(delay)


def verify_all_off(schedules):
    live = discover_schedules_with_recovery("All-off verification")

    live_by_id = {schedule["uniqueId"]: schedule for schedule in live}

    failures = []

    for schedule in schedules:
        live_schedule = live_by_id.get(schedule["uniqueId"])

        if live_schedule is None:
            failures.append(f"{schedule['serviceName']}: missing from live state")
            continue

        if live_schedule["on"]:
            failures.append(f"{schedule['serviceName']}: still ON")

    return failures


def activate_pause():
    print(f"===== {VACUUM_DISPLAY_NAME.upper()} PAUSE ACTIVATE =====")
    print()

    if os.path.exists(STATE_FILE):
        state = load_state()
        if state["pauseActive"]:
            snapshot = load_active_snapshot(state)
            successful_actions = {
                "return-to-dock-acknowledged",
                "not-required-idle-docked",
                "not-required-not-cleaning",
                "transition-confirmed",
            }
            schedules_off = all(
                item["pauseEstablishedOn"] is False
                for item in snapshot["schedules"]
            )
            action = snapshot.get("vacuumAction", {})
            if schedules_off and action.get("result") in successful_actions:
                print("Pause is already ACTIVE; existing session preserved.")
                print(f"Session: {state['sessionId']}")
                return 0
            print(
                "Pause is ACTIVE but incomplete; reconciling the existing "
                "session before reporting success."
            )
            return reconcile_active_pause()

    schedules = discover_schedules_with_recovery("Pause activation discovery")

    if not schedules:
        raise RuntimeError(f"No {VACUUM_DISPLAY_NAME} schedules were discovered")

    print(f"Schedules discovered: {len(schedules)}")
    print()

    for schedule in schedules:
        print(f"{schedule['serviceName']}: " f"{'ON' if schedule['on'] else 'OFF'}")

    print()

    sid = session_id()

    snapshot_schedules = []
    for schedule in schedules:
        saved = dict(schedule)
        saved["prePauseOn"] = bool(schedule["on"])
        saved["pauseEstablishedOn"] = bool(schedule["on"])
        snapshot_schedules.append(saved)

    snapshot = {
        "version": 3,
        "vacuumId": VACUUM_ID,
        "sessionId": sid,
        "createdAt": utc_now(),
        "scheduleCount": len(schedules),
        "schedules": snapshot_schedules,
        "vacuumAction": {
            "result": "pending-schedule-disable",
            "updatedAt": utc_now(),
        },
    }

    print("Persisting snapshot BEFORE any schedule writes...")
    persist_snapshot(snapshot)

    print()
    print("Persisting pause state as ACTIVE...")
    persist_state(True, sid)
    persist_active_snapshot(snapshot)

    print()
    print(f"===== TURNING {VACUUM_DISPLAY_NAME.upper()} SCHEDULES OFF =====")

    failures = []
    saved_by_id = {item["uniqueId"]: item for item in snapshot_schedules}
    changes = [(schedule, False) for schedule in schedules if schedule["on"]]

    for schedule in schedules:
        name = schedule["serviceName"]
        print(f"{name}: " f"{'already OFF' if not schedule['on'] else 'ON -> OFF'}")
        if not schedule["on"]:
            saved_by_id[schedule["uniqueId"]]["pauseEstablishedOn"] = False
            print("  WRITE: not required")

    if changes:
        print(f"Submitting {len(changes)} schedule writes as one coordinated batch...")
        failures.extend(set_schedules_batch(changes))

    snapshot["pauseEstablishedAt"] = utc_now()
    persist_active_snapshot(snapshot)

    print()
    print(
        f"===== FINAL {VACUUM_DISPLAY_NAME.upper()} "
        "SCHEDULE VERIFICATION ====="
    )

    try:
        final_schedules, verification_failures = verify_schedule_changes(
            [(schedule, False) for schedule in schedules]
        )
        failures.extend(verification_failures)
        final_by_id = {item["uniqueId"]: item for item in final_schedules}
        for saved in snapshot_schedules:
            live = final_by_id.get(saved["uniqueId"])
            if live is None:
                failures.append(f"{saved['serviceName']}: missing from final discovery")
                continue
            saved["pauseEstablishedOn"] = bool(live["on"])
        snapshot["pauseEstablishedAt"] = utc_now()
        persist_active_snapshot(snapshot)

    except Exception as e:
        failures.append(f"Final verification failed: {e}")

    if failures:
        print()
        print("RESULT: FAIL")
        print()
        print("Pause remains ACTIVE.")
        print("The persisted snapshot has NOT been deleted.")
        print()
        print("Failures:")

        for failure in failures:
            print(f"  - {failure}")

        print()
        print(
            "IMPORTANT: The controller will not automatically "
            "restore schedules after this failure."
        )

        return 1

    try:
        handle_active_cleaning(snapshot)
    except Exception as e:
        print()
        print("RESULT: FAIL")
        print()
        print("Pause remains ACTIVE and all schedules remain OFF.")
        print("The persisted snapshot has NOT been deleted.")
        print(f"Active-cleaning handling failed: {e}")
        return 1

    print()
    print("RESULT: PASS")
    print()
    print(f"All {len(schedules)} {VACUUM_DISPLAY_NAME} schedules are OFF.")
    print("Pause state is ACTIVE.")
    print(f"Session: {sid}")
    print()
    print(f"===== END {VACUUM_DISPLAY_NAME.upper()} PAUSE ACTIVATE =====")

    return 0


def deactivate_pause(dry_run=False):
    print(f"===== {VACUUM_DISPLAY_NAME.upper()} PAUSE DEACTIVATE =====")
    print()

    if not os.path.exists(STATE_FILE):
        raise RuntimeError("Pause state file does not exist")

    state = load_state()

    if not state.get("pauseActive"):
        raise RuntimeError(f"{VACUUM_DISPLAY_NAME} pause is not currently active")

    snapshot = load_active_snapshot(state)
    snapshot_schedules = snapshot["schedules"]

    live_schedules = discover_schedules_with_recovery("Unpause discovery")

    live_by_id = {schedule["uniqueId"]: schedule for schedule in live_schedules}

    print(f"Snapshot schedules: {len(snapshot_schedules)}")
    print(f"Live schedules:     {len(live_schedules)}")
    print()

    actions = []

    for saved in snapshot_schedules:
        unique_id = saved["uniqueId"]
        name = saved["serviceName"]
        snapshot_on = saved["prePauseOn"]
        pause_established_on = saved["pauseEstablishedOn"]

        live = live_by_id.get(unique_id)

        if live is None:
            raise RuntimeError(f"{name}: schedule is missing from live state")

        current_on = bool(live["on"])

        if current_on != pause_established_on:
            action = "KEEP CURRENT"
        elif current_on != snapshot_on:
            action = "RESTORE ON" if snapshot_on else "RESTORE OFF"
        else:
            action = "LEAVE UNCHANGED"

        actions.append(
            {
                "name": name,
                "uniqueId": unique_id,
                "snapshot_on": snapshot_on,
                "pause_established_on": pause_established_on,
                "current_on": current_on,
                "action": action,
            }
        )

    print("===== UNPAUSE DECISION =====")

    for action in actions:
        print(
            f"{action['name']}: "
            f"snapshot={'ON' if action['snapshot_on'] else 'OFF'}, "
            f"pause-established={'ON' if action['pause_established_on'] else 'OFF'}, "
            f"current={'ON' if action['current_on'] else 'OFF'} "
            f"-> {action['action']}"
        )

    print()

    if dry_run:
        print("===== DRY RUN RESULT =====")
        print("PASS")
        print()
        print("No schedule state was changed.")
        print("Pause state remains ACTIVE.")
        print(
            f"===== END {VACUUM_DISPLAY_NAME.upper()} "
            "PAUSE DEACTIVATE DRY RUN ====="
        )
        return 0

    print("===== APPLYING UNPAUSE =====")

    changes = [
        (
            {"serviceName": action["name"], "uniqueId": action["uniqueId"]},
            action["snapshot_on"],
        )
        for action in actions
        if action["action"].startswith("RESTORE ")
    ]
    for schedule, desired in changes:
        print(f"{schedule['serviceName']}: restoring {'ON' if desired else 'OFF'}")

    failures = set_schedules_batch(changes)
    _, verification_failures = verify_schedule_changes(changes)
    failures.extend(verification_failures)

    if failures:
        print()
        print("RESULT: FAIL")
        print()
        print("Pause remains ACTIVE.")
        print("Snapshot has NOT been deleted.")
        print("The coordinated batch did not verify completely.")

        for failure in failures:
            print(f"  - {failure}")

        return 1

    persist_state(False, state["sessionId"])
    try:
        os.unlink(SNAPSHOT_FILE)
    except FileNotFoundError:
        pass

    print()
    print("RESULT: PASS")
    print(f"{VACUUM_DISPLAY_NAME} pause is now INACTIVE.")
    print(f"===== END {VACUUM_DISPLAY_NAME.upper()} PAUSE DEACTIVATE =====")

    return 0


def reconcile_active_pause():
    """Read first, then safely finish an interrupted active pause transaction."""
    print(f"===== {VACUUM_DISPLAY_NAME.upper()} PAUSE RECONCILE =====")
    print()

    state = load_state()
    if not state.get("pauseActive"):
        raise RuntimeError(f"{VACUUM_DISPLAY_NAME} pause is not currently active")

    snapshot = load_active_snapshot(state)
    if snapshot["version"] < 3:
        snapshot["version"] = 3
        snapshot["vacuumAction"] = {
            "result": "pending-schedule-disable",
            "updatedAt": utc_now(),
        }
    live_schedules = discover_schedules_with_recovery("Pause reconciliation")
    saved_schedules = snapshot["schedules"]
    live_by_id = {schedule["uniqueId"]: schedule for schedule in live_schedules}
    saved_ids = {schedule["uniqueId"] for schedule in saved_schedules}

    if set(live_by_id) != saved_ids:
        raise RuntimeError("Live schedules do not match the active snapshot")

    failures = []
    changes = []
    for saved in saved_schedules:
        live = live_by_id[saved["uniqueId"]]
        if live["serviceName"] != saved["serviceName"]:
            failures.append(f"{saved['serviceName']}: service identity changed")

    if failures:
        raise RuntimeError("; ".join(failures))

    for saved in saved_schedules:
        live = live_by_id[saved["uniqueId"]]
        if live["on"]:
            changes.append((live, False))

    if changes:
        print(
            f"Operation in progress: {len(changes)} schedule(s) are still ON; "
            "resuming the interrupted disable transaction."
        )
        failures.extend(set_schedules_batch(changes))

    try:
        final_schedules, verification_failures = verify_schedule_changes(
            [(saved, False) for saved in saved_schedules]
        )
        failures.extend(verification_failures)
    except Exception as exc:
        raise RuntimeError(
            "Pause reconciliation verification recovery exhausted: " + str(exc)
        ) from exc

    if failures:
        raise RuntimeError(
            "Pause reconciliation remains incomplete: " + "; ".join(failures)
        )

    final_by_id = {schedule["uniqueId"]: schedule for schedule in final_schedules}

    for saved in saved_schedules:
        saved["pauseEstablishedOn"] = bool(final_by_id[saved["uniqueId"]]["on"])
    snapshot["pauseEstablishedAt"] = utc_now()
    persist_active_snapshot(snapshot)

    handle_active_cleaning(snapshot)

    print()
    print("RESULT: PASS")
    print(f"All {len(saved_schedules)} {VACUUM_DISPLAY_NAME} schedules are OFF.")
    print("Pause state remains ACTIVE and is recoverable.")
    print(f"===== END {VACUUM_DISPLAY_NAME.upper()} PAUSE RECONCILE =====")
    return 0


def abandon_active_pause(confirmation):
    """Clear controller state without reading or writing Homebridge accessories.

    This is an emergency escape hatch for an operator who will manually reconcile
    schedule state in the Roborock app.  Preserve the incomplete snapshot under a
    unique recovery name before making the logical pause inactive.
    """
    required_confirmation = "I-WILL-RESTORE-SCHEDULES-MANUALLY"
    if confirmation != required_confirmation:
        raise RuntimeError(
            "manual recovery requires --confirm " + required_confirmation
        )

    state = load_state()
    if not state["pauseActive"]:
        raise RuntimeError(f"{VACUUM_DISPLAY_NAME} pause is already inactive")

    # Validation deliberately accepts incomplete v3 activation metadata, but it
    # still proves that the snapshot belongs to this vacuum and active session.
    snapshot = validate_snapshot(load_json(SNAPSHOT_FILE), state["sessionId"])
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    recovery_file = f"{SNAPSHOT_FILE}.manual-recovery-{timestamp}"

    # A hard link is an exact, exclusive archive on the same runtime filesystem.
    # Only after that archive exists do we clear logical state and retire primary.
    os.link(SNAPSHOT_FILE, recovery_file)
    os.chmod(recovery_file, 0o600)
    persist_state(False, state["sessionId"])
    os.unlink(SNAPSHOT_FILE)

    print(f"{VACUUM_DISPLAY_NAME} pause is now INACTIVE.")
    print("No Homebridge or Roborock API request was made.")
    print(f"Recovery snapshot retained: {recovery_file}")
    print("The operator must now restore schedule states manually.")
    return 0


def main(config=None):
    if config is not None:
        configure(config)

    if len(sys.argv) < 2:
        print(
            "Usage: <vacuum>-pause-controller.py "
            "on|off|reconcile|abandon [--dry-run|--confirm PHRASE]",
            file=sys.stderr,
        )
        return 2

    command = sys.argv[1].lower()
    dry_run = "--dry-run" in sys.argv[2:]

    if command not in ("on", "off", "reconcile", "abandon"):
        print(f"ERROR: Unsupported command: {command}", file=sys.stderr)
        return 2

    try:
        with operation_lock():
            if command == "on":
                return activate_pause()

            if command == "reconcile":
                return reconcile_active_pause()

            if command == "abandon":
                confirmation = None
                if len(sys.argv) == 4 and sys.argv[2] == "--confirm":
                    confirmation = sys.argv[3]
                elif len(sys.argv) != 2:
                    raise RuntimeError(
                        "abandon accepts only --confirm followed by the required phrase"
                    )
                return abandon_active_pause(confirmation)

            return deactivate_pause(dry_run)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        message = str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        if "HTTP 401" in message:
            print(
                "Refresh the Homebridge API token with " "get-homebridge-token.sh.",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    print("ERROR: Use a per-vacuum controller entry point.", file=sys.stderr)
    sys.exit(2)
