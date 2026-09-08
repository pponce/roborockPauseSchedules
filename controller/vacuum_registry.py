#!/usr/bin/env python3

import json
import os
import re
from pathlib import Path


DEFAULT_ROOT = Path("/var/lib/homebridge/roborockPauseSchedules")
DEFAULT_BASE_URL = "http://127.0.0.1:8581"
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
REQUIRED_TEXT_FIELDS = (
    "id",
    "displayName",
    "scheduleNamePrefix",
    "cleaningServiceName",
    "dockedServiceName",
    "returnToDockServiceName",
)

def config_file():
    default = configured_root() / "controller" / "vacuums.json"
    return Path(os.environ.get("ROBOROCK_PAUSE_CONFIG_FILE", default))


def configured_root(default=DEFAULT_ROOT):
    value = os.environ.get("ROBOROCK_PAUSE_ROOT", str(default))
    root = Path(value)
    if not root.is_absolute():
        raise ValueError("vacuum configuration root must be absolute")
    return root


def default_homebridge(root):
    return {
        "baseUrl": DEFAULT_BASE_URL,
        "legacyTokenFile": str(root / "homebridge-access-token"),
        "tokenFile": str(root / "controller" / "homebridge-access-token"),
        "credentialsFile": str(
            root / "controller" / "homebridge-api-credentials.json"
        ),
    }


def default_systemd():
    return {
        "user": "homebridge",
        "group": "homebridge",
        "bashPath": "/usr/bin/bash",
        "homebridgeUnit": "homebridge.service",
        "onCalendar": "*-*-* 00:05:00",
    }


def validate_homebridge(config, root):
    if config is not None and not isinstance(config, dict):
        raise ValueError("Homebridge configuration must be an object")
    unknown = set(config or {}) - set(default_homebridge(root))
    if unknown:
        raise ValueError(
            "unknown Homebridge configuration field(s): " + ", ".join(sorted(unknown))
        )
    values = {**default_homebridge(root), **(config or {})}
    environment = {
        "baseUrl": "ROBOROCK_PAUSE_HOMEBRIDGE_URL",
        "legacyTokenFile": "ROBOROCK_PAUSE_LEGACY_TOKEN_FILE",
        "tokenFile": "ROBOROCK_PAUSE_TOKEN_FILE",
        "credentialsFile": "ROBOROCK_PAUSE_CREDENTIALS_FILE",
    }
    for field, variable in environment.items():
        if variable in os.environ:
            values[field] = os.environ[variable]

    base_url = values["baseUrl"]
    if not isinstance(base_url, str) or not base_url.startswith(
        ("http://", "https://")
    ):
        raise ValueError("Homebridge baseUrl must be an HTTP(S) URL")
    values["baseUrl"] = base_url.rstrip("/")
    for field in ("legacyTokenFile", "tokenFile", "credentialsFile"):
        value = values[field]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"Homebridge {field} must be an absolute path")
    return values


def validate_systemd(config):
    if config is not None and not isinstance(config, dict):
        raise ValueError("systemd configuration must be an object")
    unknown = set(config or {}) - set(default_systemd())
    if unknown:
        raise ValueError(
            "unknown systemd configuration field(s): " + ", ".join(sorted(unknown))
        )
    values = {**default_systemd(), **(config or {})}
    environment = {
        "user": "ROBOROCK_PAUSE_SERVICE_USER",
        "group": "ROBOROCK_PAUSE_SERVICE_GROUP",
        "bashPath": "ROBOROCK_PAUSE_BASH_PATH",
        "homebridgeUnit": "ROBOROCK_PAUSE_HOMEBRIDGE_UNIT",
        "onCalendar": "ROBOROCK_PAUSE_ON_CALENDAR",
    }
    for field, variable in environment.items():
        if variable in os.environ:
            values[field] = os.environ[variable]
    for field, value in values.items():
        if not isinstance(value, str) or not value or "\n" in value:
            raise ValueError(f"systemd {field} must be a non-empty single line")
    if not Path(values["bashPath"]).is_absolute():
        raise ValueError("systemd bashPath must be absolute")
    account_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    for field in ("user", "group"):
        if not account_pattern.fullmatch(values[field]):
            raise ValueError(f"systemd {field} is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", values["homebridgeUnit"]):
        raise ValueError("systemd homebridgeUnit is invalid")
    return values


def validate_vacuums(vacuums):
    if not isinstance(vacuums, list) or not vacuums:
        raise ValueError("vacuum configuration must contain a non-empty vacuums list")

    validated = []
    ids = set()
    for index, vacuum in enumerate(vacuums):
        if not isinstance(vacuum, dict):
            raise ValueError(f"vacuum configuration entry {index} is not an object")
        for field in REQUIRED_TEXT_FIELDS:
            if not isinstance(vacuum.get(field), str) or not vacuum[field]:
                raise ValueError(f"vacuum configuration entry {index} has invalid {field}")
        vacuum_id = vacuum["id"]
        if not ID_PATTERN.fullmatch(vacuum_id):
            raise ValueError(f"vacuum ID {vacuum_id!r} is not filesystem-safe")
        if vacuum_id in ids:
            raise ValueError(f"duplicate vacuum ID: {vacuum_id}")
        ids.add(vacuum_id)
        validated.append({field: vacuum[field] for field in REQUIRED_TEXT_FIELDS})
    return tuple(validated)


def load_registry():
    path = config_file()
    if not path.exists():
        raise FileNotFoundError(f"vacuum registry does not exist: {path}")

    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    registry = validate_registry_document(config)
    registry["source"] = str(path)
    return registry


def validate_registry_document(config):
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("vacuum configuration schema is invalid")
    unknown = set(config) - {"version", "root", "homebridge", "systemd", "vacuums"}
    if unknown:
        raise ValueError(
            "unknown vacuum configuration field(s): " + ", ".join(sorted(unknown))
        )

    root_value = config.get("root", str(DEFAULT_ROOT))
    if not isinstance(root_value, str) or not root_value:
        raise ValueError("vacuum configuration root is invalid")
    root = configured_root(Path(root_value))

    return {
        "root": root,
        "homebridge": validate_homebridge(config.get("homebridge"), root),
        "systemd": validate_systemd(config.get("systemd")),
        "vacuums": validate_vacuums(config.get("vacuums")),
    }


def homebridge_config(registry):
    homebridge = registry["homebridge"]
    return {
        "base_url": homebridge["baseUrl"],
        "legacy_token_file": homebridge["legacyTokenFile"],
        "token_file": homebridge["tokenFile"],
        "credentials_file": homebridge["credentialsFile"],
    }


def find_vacuum(registry, vacuum_id):
    matches = [vacuum for vacuum in registry["vacuums"] if vacuum["id"] == vacuum_id]
    if len(matches) != 1:
        raise ValueError(f"unknown vacuum ID: {vacuum_id}")
    return matches[0]


def engine_config(engine, registry, vacuum_id):
    vacuum = find_vacuum(registry, vacuum_id)
    runtime = registry["root"] / "controller"
    prefix = runtime / f"{vacuum_id}-pause"
    return engine.VacuumConfig(
        vacuum_id=vacuum_id,
        display_name=vacuum["displayName"],
        schedule_name_prefix=vacuum["scheduleNamePrefix"],
        cleaning_service_name=vacuum["cleaningServiceName"],
        docked_service_name=vacuum["dockedServiceName"],
        return_to_dock_service_name=vacuum["returnToDockServiceName"],
        state_file=str(prefix.with_name(prefix.name + "-state.json")),
        snapshot_file=str(prefix.with_name(prefix.name + "-snapshot.json")),
        backup_snapshot_file=str(
            prefix.with_name(prefix.name + "-snapshot.backup.json")
        ),
        lock_file=str(prefix.with_suffix(".lock")),
    )


def orchestrator_vacuums(registry):
    root = registry["root"]
    controller = root / "controller" / "vacuum-pause-controller.py"
    runtime = root / "controller"
    vacuums = []
    for vacuum in registry["vacuums"]:
        on_command = ["python3", str(controller), vacuum["id"], "on"]
        off_command = ["python3", str(controller), vacuum["id"], "off"]

        vacuums.append({
            "id": vacuum["id"],
            "displayName": vacuum["displayName"],
            "stateFile": str(runtime / f"{vacuum['id']}-pause-state.json"),
            "snapshotFile": str(runtime / f"{vacuum['id']}-pause-snapshot.json"),
            "onCommand": on_command,
            "offCommand": off_command,
        })
    return tuple(vacuums)
