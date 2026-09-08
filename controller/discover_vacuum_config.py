#!/usr/bin/env python3

"""Read-only Homebridge discovery for vacuum registry configuration."""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schedule_pause_controller as engine
from vacuum_registry import (
    DEFAULT_ROOT,
    configured_root,
    default_homebridge,
    default_systemd,
    homebridge_config,
    load_registry,
    validate_registry_document,
    validate_vacuums,
)


ROLE_SUFFIXES = {
    "cleaningServiceName": " Cleaning",
    "dockedServiceName": " Docked",
    "returnToDockServiceName": " Return to Dock",
}
SCHEDULE_PATTERN = re.compile(r"^(?P<base>.+) Schedule (?P<sequence>.+)$")


def characteristics(accessory, characteristic_type):
    return [
        characteristic
        for characteristic in accessory.get("serviceCharacteristics") or []
        if characteristic.get("type") == characteristic_type
    ]


def configured_name(accessory):
    matches = characteristics(accessory, "ConfiguredName")
    return matches[0].get("value") if len(matches) == 1 else None


def validate_characteristic(accessory, characteristic_type, writable=False):
    matches = characteristics(accessory, characteristic_type)
    if len(matches) != 1:
        return f"expected one {characteristic_type} characteristic, found {len(matches)}"
    permissions = matches[0].get("perms") or []
    if "pr" not in permissions:
        return f"{characteristic_type} is not readable"
    if writable and "pw" not in permissions:
        return f"{characteristic_type} is not writable"
    return None


def service_index(accessories):
    index = {}
    for accessory in accessories:
        service_name = accessory.get("serviceName")
        if isinstance(service_name, str) and service_name:
            index.setdefault(service_name, []).append(accessory)
    return index


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "vacuum"


def unique_local_ids(bases):
    used = set()
    result = {}
    for base in sorted(bases):
        candidate = slugify(base.removesuffix(" Rock"))
        local_id = candidate
        suffix = 2
        while local_id in used:
            local_id = f"{candidate}-{suffix}"
            suffix += 1
        used.add(local_id)
        result[base] = local_id
    return result


def discover_candidates(accessories):
    if not isinstance(accessories, list):
        raise ValueError("Homebridge accessory response is not a list")

    index = service_index(accessories)
    schedules_by_base = {}
    for service_name, matches in index.items():
        schedule_match = SCHEDULE_PATTERN.fullmatch(service_name)
        if not schedule_match:
            continue
        base = schedule_match.group("base")
        if any(base + suffix in index for suffix in ROLE_SUFFIXES.values()):
            schedules_by_base.setdefault(base, []).extend(matches)

    local_ids = unique_local_ids(schedules_by_base)
    candidates = []
    for base in sorted(schedules_by_base):
        issues = []
        schedules = schedules_by_base[base]
        schedule_names = sorted(
            accessory.get("serviceName") for accessory in schedules
        )
        if len(schedule_names) != len(set(schedule_names)):
            issues.append("schedule service names are not unique")
        schedule_unique_ids = [schedule.get("uniqueId") for schedule in schedules]
        if len(schedule_unique_ids) != len(set(schedule_unique_ids)):
            issues.append("schedule uniqueIds are not unique")
        for schedule in schedules:
            service_name = schedule.get("serviceName")
            issue = validate_characteristic(schedule, "On", writable=True)
            if issue:
                issues.append(f"{service_name}: {issue}")
            unique_id = schedule.get("uniqueId")
            if not isinstance(unique_id, str) or not unique_id:
                issues.append(f"{service_name}: uniqueId is missing")

        roles = {}
        for field, suffix in ROLE_SUFFIXES.items():
            service_name = base + suffix
            matches = index.get(service_name, [])
            if len(matches) != 1:
                issues.append(
                    f"{service_name}: expected exactly one service, found {len(matches)}"
                )
                continue
            role = matches[0]
            unique_id = role.get("uniqueId")
            if not isinstance(unique_id, str) or not unique_id:
                issues.append(f"{service_name}: uniqueId is missing")
            characteristic_type = (
                "On" if field == "returnToDockServiceName" else "ContactSensorState"
            )
            issue = validate_characteristic(
                role,
                characteristic_type,
                writable=field == "returnToDockServiceName",
            )
            if issue:
                issues.append(f"{service_name}: {issue}")
            roles[field] = service_name

        entry = {
            "id": local_ids[base],
            "displayName": base.removesuffix(" Rock"),
            "scheduleNamePrefix": f"{base} Schedule ",
            **roles,
        }
        candidates.append(
            {
                "base": base,
                "entry": entry,
                "scheduleCount": len(schedules),
                "schedules": [
                    {
                        "serviceName": accessory.get("serviceName"),
                        "configuredName": configured_name(accessory),
                        "uniqueId": accessory.get("uniqueId"),
                    }
                    for accessory in sorted(
                        schedules, key=lambda item: item.get("serviceName", "")
                    )
                ],
                "issues": issues,
            }
        )
    return candidates


def proposal_from_candidates(
    candidates,
    root=DEFAULT_ROOT,
    homebridge=None,
    systemd=None,
):
    complete = [
        candidate["entry"] for candidate in candidates if not candidate["issues"]
    ]
    if not complete:
        raise ValueError("no complete vacuum candidates were discovered")
    validate_vacuums(complete)
    proposal = {"version": 1, "root": str(root)}
    if homebridge is not None:
        proposal["homebridge"] = homebridge
    if systemd is not None:
        proposal["systemd"] = systemd
    proposal["vacuums"] = complete
    return proposal


def validate_registry_against_accessories(registry, accessories):
    index = service_index(accessories)
    errors = []
    for vacuum in registry["vacuums"]:
        label = vacuum["displayName"]
        schedules = [
            accessory
            for accessory in accessories
            if (accessory.get("serviceName") or "").startswith(
                vacuum["scheduleNamePrefix"]
            )
        ]
        if not schedules:
            errors.append(f"{label}: no schedule services match the configured prefix")
        schedule_unique_ids = [schedule.get("uniqueId") for schedule in schedules]
        if any(
            not isinstance(unique_id, str) or not unique_id
            for unique_id in schedule_unique_ids
        ):
            errors.append(f"{label}: one or more schedule uniqueIds are missing")
        if len(schedule_unique_ids) != len(set(schedule_unique_ids)):
            errors.append(f"{label}: schedule uniqueIds are not unique")
        for schedule in schedules:
            issue = validate_characteristic(schedule, "On", writable=True)
            if issue:
                errors.append(f"{label}: {schedule.get('serviceName')}: {issue}")

        for field, characteristic_type, writable in (
            ("cleaningServiceName", "ContactSensorState", False),
            ("dockedServiceName", "ContactSensorState", False),
            ("returnToDockServiceName", "On", True),
        ):
            service_name = vacuum[field]
            matches = index.get(service_name, [])
            if len(matches) != 1:
                errors.append(
                    f"{label}: {service_name}: expected exactly one service, "
                    f"found {len(matches)}"
                )
                continue
            unique_id = matches[0].get("uniqueId")
            if not isinstance(unique_id, str) or not unique_id:
                errors.append(f"{label}: {service_name}: uniqueId is missing")
            issue = validate_characteristic(matches[0], characteristic_type, writable)
            if issue:
                errors.append(f"{label}: {service_name}: {issue}")
    return errors


def load_accessories(path=None, registry=None):
    if path is None:
        registry = registry or load_registry()
        engine.configure_homebridge(**homebridge_config(registry))
        return engine.get_accessories()
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, list) else payload.get("accessories")


def write_proposal(proposal, output_path=None):
    serialized = json.dumps(proposal, indent=2) + "\n"
    if output_path is None:
        sys.stdout.write(serialized)
        return
    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")


def print_report(candidates):
    for candidate in candidates:
        status = "COMPLETE" if not candidate["issues"] else "INCOMPLETE"
        print(f"{candidate['base']}: {status}")
        print(f"  proposed ID: {candidate['entry']['id']}")
        print(f"  schedules: {candidate['scheduleCount']}")
        for schedule in candidate["schedules"]:
            print(
                f"    {schedule['serviceName']} | "
                f"ConfiguredName={schedule['configuredName']!r} | "
                f"uniqueId={schedule['uniqueId']!r}"
            )
        for issue in candidate["issues"]:
            print(f"  ERROR: {issue}")


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(
        description="Discover a read-only vacuum registry proposal from Homebridge"
    )
    parser.add_argument("--input", help="offline accessory JSON instead of Homebridge")
    parser.add_argument("--output", help="write proposal to a new file")
    parser.add_argument(
        "--root",
        default=None,
        help="absolute installation root recorded in the proposal",
    )
    parser.add_argument("--report", action="store_true", help="print local identifiers")
    parser.add_argument(
        "--validate", metavar="FILE", help="validate registry against discovery"
    )
    return parser.parse_args(arguments)


def main(arguments=None):
    args = parse_args(arguments)
    try:
        try:
            installation = load_registry()
        except FileNotFoundError:
            root = configured_root()
            installation = {
                "root": root,
                "homebridge": default_homebridge(root),
                "systemd": default_systemd(),
            }
        accessories = load_accessories(args.input, installation)
        if not isinstance(accessories, list):
            raise ValueError("Homebridge accessory response is not a list")
        if args.validate:
            with Path(args.validate).open(encoding="utf-8") as handle:
                raw_registry = json.load(handle)
            registry = validate_registry_document(raw_registry)
            vacuums = registry["vacuums"]
            errors = validate_registry_against_accessories(registry, accessories)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            print(f"PASS: {len(vacuums)} configured vacuum(s) validated.")
            return 0

        candidates = discover_candidates(accessories)
        if args.report:
            print_report(candidates)
            return (
                0
                if candidates and all(not item["issues"] for item in candidates)
                else 1
            )
        root = Path(args.root) if args.root else installation["root"]
        if not root.is_absolute():
            raise ValueError("proposal root must be absolute")
        proposal = proposal_from_candidates(
            candidates,
            root,
            installation["homebridge"],
            installation["systemd"],
        )
        write_proposal(proposal, args.output)
        return 0
    except (FileExistsError, OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
