#!/usr/bin/env python3

"""Guarded staging and installation workflow for Roborock Pause Schedules."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discover_vacuum_config as discovery
from render_systemd_units import render_units
from vacuum_registry import (
    REQUIRED_TEXT_FIELDS,
    config_file,
    configured_root,
    default_homebridge,
    default_systemd,
    load_registry,
    validate_registry_document,
)


INSTALLER_VERSION = 1
UNIFIED_MANIFEST_VERSION = 2
STAGE_MANIFEST = "stage-manifest.json"
INSTALLED_MANIFEST = "installer-manifest.json"
REGISTRY_NAME = "vacuums.json"
COMMANDS_NAME = "command-switches.json"
SYSTEMD_DIRECTORY = "systemd"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def write_new(path, contents, mode=0o644):
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            # os.open applies the caller's umask. Installer artifact modes are
            # part of the public contract, so establish the requested final
            # mode explicitly before publishing or returning the file.
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def replace_file(path, contents, mode):
    """Atomically replace a regular file without following a target symlink."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"replacement target is missing or unsafe: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    write_new(temporary, contents, mode)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def command_switches(registry):
    root = registry["root"]
    python = "python3"
    controller = root / "controller" / "vacuum-pause-controller.py"
    switches = []
    for vacuum in registry["vacuums"]:
        prefix = [python, str(controller), vacuum["id"]]
        switches.append(
            {
                "name": f"{vacuum['displayName']} Pause Schedules",
                "id": f"{vacuum['id']}-pause-schedules",
                "on": shlex.join([*prefix, "on"]),
                "off": shlex.join([*prefix, "off"]),
                "state": shlex.join([*prefix, "state"]),
                "onValue": "true",
            }
        )
    switches.extend(
        [
            {
                "name": "Pause All Vacuum Schedules",
                "id": "all-vacuums-pause-schedules",
                "on": str(root / "all-vacuums-pause-on.sh"),
                "off": str(root / "all-vacuums-pause-off.sh"),
                "state": str(root / "all-vacuums-pause-state.sh"),
                "onValue": "true",
            },
            {
                "name": "Pause Until Tomorrow",
                "id": "pause-until-tomorrow",
                "on": str(root / "pause-until-tomorrow-on.sh"),
                "off": str(root / "pause-until-tomorrow-off.sh"),
                "state": str(root / "pause-until-tomorrow-state.sh"),
                "onValue": "true",
            },
        ]
    )
    return {
        "version": 1,
        "contract": {
            "on": "exit zero only after verified activation",
            "off": "exit zero only after verified restoration",
            "state": "print exactly true or false and exit zero",
        },
        "switches": switches,
    }


def required_runtime_files(root):
    return (
        root / "controller" / "schedule_pause_controller.py",
        root / "controller" / "vacuum_registry.py",
        root / "controller" / "vacuum-pause-controller.py",
        root / "controller" / "all-vacuums-pause-controller.py",
        root / "controller" / "pause-until-tomorrow-controller.py",
        root / "controller" / "pause_until_tomorrow.py",
        root / "all-vacuums-pause-on.sh",
        root / "all-vacuums-pause-off.sh",
        root / "all-vacuums-pause-state.sh",
        root / "all-vacuums-pause-expire.sh",
        root / "pause-until-tomorrow-on.sh",
        root / "pause-until-tomorrow-off.sh",
        root / "pause-until-tomorrow-state.sh",
    )


def check_runtime(registry):
    errors = []
    root = registry["root"]
    if not root.is_dir():
        errors.append(f"installation root is not a directory: {root}")
    for path in required_runtime_files(root):
        if not path.is_file():
            errors.append(f"required runtime file is missing: {path}")
        elif not os.access(path, os.X_OK):
            errors.append(f"required runtime file is not executable: {path}")
    return errors


def discover_registry(input_path=None):
    root = configured_root()
    installation = {
        "root": root,
        "homebridge": default_homebridge(root),
        "systemd": default_systemd(),
    }
    accessories = discovery.load_accessories(input_path, installation)
    candidates = discovery.discover_candidates(accessories)
    return discovery.proposal_from_candidates(
        candidates,
        installation["root"],
        installation["homebridge"],
        installation["systemd"],
    ), accessories


def stage(output_directory, input_path=None, registry_path=None):
    output = Path(output_directory)
    if output.exists():
        raise FileExistsError(f"staging directory already exists: {output}")
    if registry_path:
        with Path(registry_path).open(encoding="utf-8") as handle:
            registry_document = json.load(handle)
        registry = validate_registry_document(registry_document)
    else:
        registry_document, accessories = discover_registry(input_path)
        registry = validate_registry_document(registry_document)
        validation_errors = discovery.validate_registry_against_accessories(
            registry, accessories
        )
        if validation_errors:
            raise RuntimeError("; ".join(validation_errors))

    output.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    try:
        artifacts = {
            REGISTRY_NAME: json_bytes(registry_document),
            COMMANDS_NAME: json_bytes(command_switches(registry)),
        }
        for name, contents in render_units(registry).items():
            artifacts[f"{SYSTEMD_DIRECTORY}/{name}"] = contents.encode("utf-8")
        for relative, contents in artifacts.items():
            mode = 0o600 if relative == REGISTRY_NAME else 0o644
            write_new(output / relative, contents, mode)
        manifest = {
            "version": INSTALLER_VERSION,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "artifacts": {
                relative: sha256(output / relative) for relative in sorted(artifacts)
            },
        }
        write_new(output / STAGE_MANIFEST, json_bytes(manifest), 0o600)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    return output


def validate_stage(stage_directory):
    stage_path = Path(stage_directory)
    manifest_path = stage_path / STAGE_MANIFEST
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or manifest.get("version") != INSTALLER_VERSION:
        raise ValueError("stage manifest schema is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("stage manifest artifact list is invalid")
    required = {
        REGISTRY_NAME,
        COMMANDS_NAME,
        f"{SYSTEMD_DIRECTORY}/roborock-pause-until-tomorrow.service",
        f"{SYSTEMD_DIRECTORY}/roborock-pause-until-tomorrow.timer",
    }
    if set(artifacts) != required:
        raise ValueError("stage manifest does not contain the required artifacts")
    for relative, expected_hash in artifacts.items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError(f"unsafe staged artifact path: {relative}")
        path = stage_path / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected_hash:
            raise ValueError(f"staged artifact is missing or modified: {relative}")
    with (stage_path / REGISTRY_NAME).open(encoding="utf-8") as handle:
        registry_document = json.load(handle)
    registry = validate_registry_document(registry_document)
    return manifest, registry


def initial_state(vacuum_id):
    return {
        "version": 1,
        "vacuumId": vacuum_id,
        "pauseActive": False,
        "sessionId": (
            f"{vacuum_id}-installed-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ),
    }


def state_path(registry, vacuum_id):
    return registry["root"] / "controller" / f"{vacuum_id}-pause-state.json"


def validate_state_file(path, vacuum_id, require_inactive=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"state file is missing or unsafe: {path}")
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    if (
        not isinstance(state, dict)
        or state.get("version") != 1
        or state.get("vacuumId") != vacuum_id
        or not isinstance(state.get("pauseActive"), bool)
        or not isinstance(state.get("sessionId"), str)
        or not state["sessionId"]
    ):
        raise ValueError(f"state file schema is invalid: {path}")
    if require_inactive and state["pauseActive"]:
        raise ValueError(f"operation requires an inactive pause state: {path}")
    return state


def install_plan(stage_directory, systemd_directory=None):
    manifest, registry = validate_stage(stage_directory)
    root = registry["root"]
    actions = [
        {
            "source": str(Path(stage_directory) / REGISTRY_NAME),
            "target": str(root / "controller" / REGISTRY_NAME),
            "kind": "registry",
        }
    ]
    for vacuum in registry["vacuums"]:
        actions.append(
            {
                "target": str(
                    root / "controller" / f"{vacuum['id']}-pause-state.json"
                ),
                "kind": "state",
                "vacuum": vacuum["id"],
            }
        )
    if systemd_directory:
        for unit in (
            "roborock-pause-until-tomorrow.service",
            "roborock-pause-until-tomorrow.timer",
        ):
            actions.append(
                {
                    "source": str(Path(stage_directory) / SYSTEMD_DIRECTORY / unit),
                    "target": str(Path(systemd_directory) / unit),
                    "kind": "systemd",
                }
            )
    actions.append(
        {
            "target": str(root / "controller" / INSTALLED_MANIFEST),
            "kind": "manifest",
        }
    )
    return manifest, registry, actions


def ensure_targets_absent(actions):
    existing = [action["target"] for action in actions if Path(action["target"]).exists()]
    if existing:
        raise FileExistsError(
            "refusing to replace existing installation target(s): "
            + ", ".join(existing)
        )


def apply_install(stage_directory, systemd_directory=None):
    stage_manifest, registry, actions = install_plan(
        stage_directory, systemd_directory
    )
    runtime_errors = check_runtime(registry)
    if runtime_errors:
        raise RuntimeError("; ".join(runtime_errors))
    ensure_targets_absent(actions)
    created = []
    try:
        installed_files = []
        for action in actions:
            if action["kind"] == "manifest":
                continue
            target = Path(action["target"])
            if action["kind"] == "state":
                contents = json_bytes(initial_state(action["vacuum"]))
                mode = 0o600
            else:
                contents = Path(action["source"]).read_bytes()
                mode = 0o644
            write_new(target, contents, mode)
            created.append(target)
            installed_files.append(
                {"path": str(target), "kind": action["kind"], "sha256": sha256(target)}
            )
        installed_manifest = {
            "version": INSTALLER_VERSION,
            "installedAt": datetime.now(timezone.utc).isoformat(),
            "stageArtifacts": stage_manifest["artifacts"],
            "installedFiles": installed_files,
            "preserveOnUninstall": [
                item["path"] for item in installed_files if item["kind"] == "state"
            ],
        }
        manifest_target = Path(actions[-1]["target"])
        write_new(manifest_target, json_bytes(installed_manifest), 0o600)
        created.append(manifest_target)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return actions


def print_actions(actions, heading):
    print(heading)
    for action in actions:
        source = f" <- {action['source']}" if action.get("source") else ""
        print(f"  {action['kind']}: {action['target']}{source}")


def installed_manifest_path():
    return load_registry()["root"] / "controller" / INSTALLED_MANIFEST


def load_installed_manifest():
    path = installed_manifest_path()
    with path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("installed manifest schema is invalid")
    if manifest.get("version") == UNIFIED_MANIFEST_VERSION:
        validate_unified_manifest(manifest)
        return path, manifest
    if manifest.get("version") != INSTALLER_VERSION:
        raise ValueError("installed manifest schema is invalid")
    files = manifest.get("installedFiles")
    if not isinstance(files, list) or not files:
        raise ValueError("installed manifest file list is invalid")
    return path, manifest


def verify_installed(live=False):
    registry = load_registry()
    errors = check_runtime(registry)
    try:
        _, manifest = load_installed_manifest()
        managed = manifest.get("managedFiles", manifest.get("installedFiles", []))
        for item in managed:
            if item.get("kind") == "state":
                continue
            path = Path(item.get("path", ""))
            if (
                path.is_symlink()
                or not path.is_file()
                or sha256(path) != item.get("sha256")
            ):
                errors.append(f"installer-owned file is missing or modified: {path}")
        for item in manifest.get("observedFiles", []):
            path = Path(item.get("path", ""))
            if (
                path.is_symlink()
                or not path.is_file()
                or sha256(path) != item.get("sha256")
            ):
                errors.append(f"observed file is missing or modified: {path}")
        if manifest.get("version") == UNIFIED_MANIFEST_VERSION:
            registry_path = Path(manifest["registry"]["path"])
            expected_states = sorted(
                str(state_path(registry, vacuum["id"]))
                for vacuum in registry["vacuums"]
            )
            if registry_path != config_file():
                errors.append("unified manifest registry path does not match configuration")
            elif sha256(registry_path) != manifest["registry"]["proposedSha256"]:
                errors.append("unified manifest registry hash does not match")
            if manifest["vacuumIds"] != [
                vacuum["id"] for vacuum in registry["vacuums"]
            ]:
                errors.append("unified manifest vacuum IDs do not match registry")
            if manifest["preservedFiles"] != expected_states:
                errors.append("unified manifest preserved state files do not match registry")
    except (OSError, TypeError, ValueError) as exc:
        errors.append(f"invalid installed manifest: {exc}")
    for vacuum in registry["vacuums"]:
        path = state_path(registry, vacuum["id"])
        try:
            validate_state_file(path, vacuum["id"])
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"invalid state file {path}: {exc}")
    if live:
        accessories = discovery.load_accessories(registry=registry)
        errors.extend(discovery.validate_registry_against_accessories(registry, accessories))
    return errors


def uninstall_plan(systemd_directory=None):
    manifest_path, manifest = load_installed_manifest()
    actions = []
    managed = manifest.get("managedFiles", manifest.get("installedFiles", []))
    for item in managed:
        if item.get("kind") == "state":
            continue
        if item.get("kind") == "systemd" and systemd_directory is None:
            continue
        if item.get("kind") == "systemd" and Path(item["path"]).parent != Path(
            systemd_directory
        ):
            raise ValueError(
                "requested systemd directory does not match installed manifest"
            )
        actions.append({**item, "target": item["path"]})
    actions.append({"kind": "manifest", "target": str(manifest_path)})
    return actions


def apply_uninstall(actions):
    for action in actions:
        path = Path(action["target"])
        if action["kind"] != "manifest":
            if path.is_symlink() or not path.is_file() or sha256(path) != action[
                "sha256"
            ]:
                raise RuntimeError(f"refusing to remove modified file: {path}")
    for action in actions:
        Path(action["target"]).unlink(missing_ok=True)


def registry_vacuum_diff(current_registry, proposed_registry):
    """Return a deterministic, ID-based diff between two validated registries."""
    current = {item["id"]: item for item in current_registry["vacuums"]}
    proposed = {item["id"]: item for item in proposed_registry["vacuums"]}
    current_ids = set(current)
    proposed_ids = set(proposed)
    changed = []
    for vacuum_id in sorted(current_ids & proposed_ids):
        fields = {
            field: {"from": current[vacuum_id][field], "to": proposed[vacuum_id][field]}
            for field in REQUIRED_TEXT_FIELDS
            if field != "id" and current[vacuum_id][field] != proposed[vacuum_id][field]
        }
        if fields:
            changed.append({"id": vacuum_id, "fields": fields})
    return {
        "added": sorted(proposed_ids - current_ids),
        "removed": sorted(current_ids - proposed_ids),
        "changed": changed,
        "unchanged": sorted(
            vacuum_id
            for vacuum_id in current_ids & proposed_ids
            if current[vacuum_id] == proposed[vacuum_id]
        ),
    }


def recovery_artifacts(root, vacuum_id):
    runtime = Path(root) / "controller"
    prefix = runtime / f"{vacuum_id}-pause"
    candidates = [
        prefix.with_name(prefix.name + "-state.json"),
        prefix.with_name(prefix.name + "-snapshot.json"),
        prefix.with_name(prefix.name + "-snapshot.backup.json"),
    ]
    candidates.extend(
        sorted(runtime.glob(f"{vacuum_id}-pause-snapshot.json.manual-recovery-*"))
    )
    return [str(path) for path in candidates if path.exists()]


def validate_unified_manifest(manifest):
    required = {
        "version",
        "installationMode",
        "migratedFrom",
        "registry",
        "vacuumIds",
        "managedFiles",
        "preservedFiles",
        "observedFiles",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError("unified manifest fields are invalid")
    if manifest["version"] != UNIFIED_MANIFEST_VERSION:
        raise ValueError("unified manifest version is invalid")
    if manifest["installationMode"] != "managed":
        raise ValueError("unified manifest installation mode is invalid")
    migrated = manifest["migratedFrom"]
    if (
        not isinstance(migrated, dict)
        or migrated.get("version") != INSTALLER_VERSION
        or not isinstance(migrated.get("installationMode"), str)
        or not migrated["installationMode"]
        or not isinstance(migrated.get("manifestPath"), str)
        or not migrated["manifestPath"]
    ):
        raise ValueError("unified manifest migration provenance is invalid")
    registry = manifest["registry"]
    if (
        not isinstance(registry, dict)
        or set(registry) != {"path", "proposedSha256"}
        or not isinstance(registry["path"], str)
        or not registry["path"]
        or not isinstance(registry["proposedSha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", registry["proposedSha256"])
    ):
        raise ValueError("unified manifest registry record is invalid")
    vacuum_ids = manifest["vacuumIds"]
    if (
        not isinstance(vacuum_ids, list)
        or not vacuum_ids
        or len(vacuum_ids) != len(set(vacuum_ids))
        or any(not isinstance(item, str) or not item for item in vacuum_ids)
    ):
        raise ValueError("unified manifest vacuum IDs are invalid")
    for field in ("managedFiles", "preservedFiles", "observedFiles"):
        if not isinstance(manifest[field], list):
            raise ValueError(f"unified manifest {field} is invalid")
    return manifest


def unified_migration_plan(stage_directory):
    """Plan, but never apply, migration from a v1 installation manifest."""
    _, proposed = validate_stage(stage_directory)
    current = load_registry()
    if current["root"] != proposed["root"]:
        raise ValueError("proposed registry changes the installation root")
    if current["homebridge"] != proposed["homebridge"]:
        raise ValueError("proposed registry changes Homebridge settings")
    if current["systemd"] != proposed["systemd"]:
        raise ValueError("proposed registry changes systemd settings")

    manifest_path, manifest = load_installed_manifest()
    if manifest.get("version") != INSTALLER_VERSION:
        raise ValueError("unified migration requires a version 1 manifest")

    errors = verify_installed(live=False)
    if errors:
        raise RuntimeError("; ".join(errors))

    diff = registry_vacuum_diff(current, proposed)
    root = current["root"]
    current_ids = {vacuum["id"] for vacuum in current["vacuums"]}
    for vacuum_id in sorted(current_ids):
        state = validate_state_file(
            state_path(current, vacuum_id), vacuum_id, require_inactive=True
        )
        primary = root / "controller" / f"{vacuum_id}-pause-snapshot.json"
        if primary.exists():
            raise ValueError(
                f"unified migration requires no active snapshot: {primary}"
            )
        if state["pauseActive"]:
            raise ValueError(f"unified migration requires {vacuum_id} inactive")

    additions = []
    for vacuum_id in diff["added"]:
        target = state_path(proposed, vacuum_id)
        if target.exists():
            raise FileExistsError(f"added vacuum state target already exists: {target}")
        additions.append({"id": vacuum_id, "stateFile": str(target)})

    removals = [
        {
            "id": vacuum_id,
            "archiveRequired": recovery_artifacts(root, vacuum_id),
        }
        for vacuum_id in diff["removed"]
    ]

    installed_mode = manifest.get("installationMode", "installed")
    registry_path = root / "controller" / REGISTRY_NAME
    proposed_registry_hash = sha256(Path(stage_directory) / REGISTRY_NAME)
    managed_files = []
    for item in manifest["installedFiles"]:
        if item.get("kind") == "state":
            continue
        normalized = dict(item)
        if normalized.get("kind") == "registry":
            normalized["path"] = str(registry_path)
            normalized["sha256"] = proposed_registry_hash
        managed_files.append(normalized)
    preserved_files = sorted(
        str(state_path(proposed, vacuum["id"]))
        for vacuum in proposed["vacuums"]
    )
    proposed_manifest = {
        "version": UNIFIED_MANIFEST_VERSION,
        "installationMode": "managed",
        "migratedFrom": {
            "version": manifest["version"],
            "installationMode": installed_mode,
            "manifestPath": str(manifest_path),
        },
        "registry": {
            "path": str(registry_path),
            "proposedSha256": proposed_registry_hash,
        },
        "vacuumIds": [vacuum["id"] for vacuum in proposed["vacuums"]],
        "managedFiles": managed_files,
        "preservedFiles": preserved_files,
        "observedFiles": manifest.get("observedFiles", []),
    }
    validate_unified_manifest(proposed_manifest)
    return {
        "planVersion": 1,
        "operation": "migrate-to-unified-manifest-v2",
        "appliesChanges": False,
        "currentManifest": str(manifest_path),
        "registryDiff": diff,
        "additions": additions,
        "removals": removals,
        "proposedManifest": proposed_manifest,
    }


def unified_reconfiguration_plan(stage_directory):
    """Plan, but never apply, a registry change for a managed v2 installation."""
    _, proposed = validate_stage(stage_directory)
    current = load_registry()
    if current["root"] != proposed["root"]:
        raise ValueError("proposed registry changes the installation root")
    if current["homebridge"] != proposed["homebridge"]:
        raise ValueError("proposed registry changes Homebridge settings")
    if current["systemd"] != proposed["systemd"]:
        raise ValueError("proposed registry changes systemd settings")

    manifest_path, manifest = load_installed_manifest()
    if manifest.get("version") != UNIFIED_MANIFEST_VERSION:
        raise ValueError("managed reconfiguration requires a version 2 manifest")
    errors = verify_installed(live=False)
    if errors:
        raise RuntimeError("; ".join(errors))

    diff = registry_vacuum_diff(current, proposed)
    root = current["root"]
    current_ids = {vacuum["id"] for vacuum in current["vacuums"]}
    for vacuum_id in sorted(current_ids):
        validate_state_file(
            state_path(current, vacuum_id), vacuum_id, require_inactive=True
        )
        primary = root / "controller" / f"{vacuum_id}-pause-snapshot.json"
        if primary.exists():
            raise ValueError(
                f"managed reconfiguration requires no active snapshot: {primary}"
            )

    additions = []
    for vacuum_id in diff["added"]:
        target = state_path(proposed, vacuum_id)
        if target.exists():
            raise FileExistsError(f"added vacuum state target already exists: {target}")
        additions.append({"id": vacuum_id, "stateFile": str(target)})
    removals = [
        {
            "id": vacuum_id,
            "archiveRequired": recovery_artifacts(root, vacuum_id),
        }
        for vacuum_id in diff["removed"]
    ]

    proposed_registry_hash = sha256(Path(stage_directory) / REGISTRY_NAME)
    registry_path = root / "controller" / REGISTRY_NAME
    managed_files = []
    for item in manifest["managedFiles"]:
        normalized = dict(item)
        if normalized.get("kind") == "registry":
            normalized["path"] = str(registry_path)
            normalized["sha256"] = proposed_registry_hash
        managed_files.append(normalized)
    proposed_manifest = {
        **manifest,
        "registry": {
            "path": str(registry_path),
            "proposedSha256": proposed_registry_hash,
        },
        "vacuumIds": [vacuum["id"] for vacuum in proposed["vacuums"]],
        "managedFiles": managed_files,
        "preservedFiles": sorted(
            str(state_path(proposed, vacuum["id"]))
            for vacuum in proposed["vacuums"]
        ),
    }
    validate_unified_manifest(proposed_manifest)
    current_order = [vacuum["id"] for vacuum in current["vacuums"]]
    proposed_order = [vacuum["id"] for vacuum in proposed["vacuums"]]
    order_changed = current_order != proposed_order
    has_changes = bool(
        diff["added"] or diff["removed"] or diff["changed"] or order_changed
    )
    return {
        "planVersion": 1,
        "operation": "reconfigure-unified-manifest-v2",
        "appliesChanges": False,
        "hasChanges": has_changes,
        "orderChanged": order_changed,
        "currentManifest": str(manifest_path),
        "registryDiff": diff,
        "additions": additions,
        "removals": removals,
        "proposedManifest": proposed_manifest,
    }


@contextmanager
def migration_locks(current, proposed):
    """Serialize migration with the orchestrator and every affected vacuum."""
    root = current["root"]
    vacuum_ids = sorted(
        {item["id"] for item in current["vacuums"]}
        | {item["id"] for item in proposed["vacuums"]}
    )
    paths = [root / "controller" / "all-vacuums-pause.lock"]
    paths.extend(
        root / "controller" / f"{vacuum_id}-pause.lock"
        for vacuum_id in vacuum_ids
    )
    with ExitStack() as stack:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = stack.enter_context(path.open("a+", encoding="utf-8"))
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def apply_unified_change(stage_directory, planner):
    """Apply a planned registry/manifest change as a rollback-safe transaction."""
    _, proposed = validate_stage(stage_directory)
    current = load_registry()
    with migration_locks(current, proposed):
        # Rebuild the complete plan under lock immediately before mutation.
        plan = planner(stage_directory)
        manifest_path = Path(plan["currentManifest"])
        registry_path = Path(plan["proposedManifest"]["registry"]["path"])
        old_manifest = manifest_path.read_bytes()
        old_registry = registry_path.read_bytes()
        created = []
        moved = []
        archive = None
        try:
            if plan["removals"]:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                archive = current["root"] / "controller" / f"removed-vacuums-{stamp}"
                archive.mkdir(mode=0o700)
                created.append(archive)
                for removal in plan["removals"]:
                    vacuum_directory = archive / removal["id"]
                    vacuum_directory.mkdir(mode=0o700)
                    for source_name in removal["archiveRequired"]:
                        source = Path(source_name)
                        target = vacuum_directory / source.name
                        os.replace(source, target)
                        moved.append((source, target))

            for addition in plan["additions"]:
                target = Path(addition["stateFile"])
                write_new(target, json_bytes(initial_state(addition["id"])), 0o600)
                created.append(target)

            proposed_registry = Path(stage_directory) / REGISTRY_NAME
            replace_file(registry_path, proposed_registry.read_bytes(), 0o644)
            replace_file(
                manifest_path,
                json_bytes(plan["proposedManifest"]),
                0o600,
            )
            errors = verify_installed(live=False)
            if errors:
                raise RuntimeError(
                    "post-migration verification failed: " + "; ".join(errors)
                )
        except Exception:
            if manifest_path.exists():
                replace_file(manifest_path, old_manifest, 0o600)
            if registry_path.exists():
                replace_file(registry_path, old_registry, 0o644)
            for source, target in reversed(moved):
                if target.exists():
                    os.replace(target, source)
            for path in reversed(created):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            raise
    return {"plan": plan, "archive": str(archive) if archive else None}


def apply_unified_migration(stage_directory):
    """Apply a validated v1-to-v2 migration as a rollback-safe local transaction."""
    return apply_unified_change(stage_directory, unified_migration_plan)


def apply_unified_reconfiguration(stage_directory):
    """Apply a validated managed-v2 registry change."""
    return apply_unified_change(stage_directory, unified_reconfiguration_plan)


def run_git(root, *arguments, check=True):
    """Run Git without a shell and return stripped standard output."""
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git {' '.join(arguments)} failed: {detail}")
    return completed


def source_upgrade_plan(target, root=None):
    """Return a no-source-write plan for a previously fetched Git target."""
    registry = load_registry()
    checkout = Path(root or registry["root"]).resolve()
    if not (checkout / ".git").is_dir():
        raise ValueError(f"installation root is not a Git checkout: {checkout}")
    status = run_git(
        checkout, "status", "--porcelain", "--untracked-files=no"
    ).stdout.strip()
    if status:
        raise ValueError("source upgrade requires a clean tracked checkout")
    current = run_git(checkout, "rev-parse", "HEAD").stdout.strip()
    target_commit = run_git(checkout, "rev-parse", "--verify", target).stdout.strip()
    ancestor = run_git(
        checkout, "merge-base", "--is-ancestor", current, target_commit, check=False
    )
    if ancestor.returncode != 0:
        raise ValueError("source upgrade target is not a fast-forward")
    _, manifest = load_installed_manifest()
    if manifest.get("version") != UNIFIED_MANIFEST_VERSION:
        raise ValueError("managed source upgrade requires a version 2 manifest")
    errors = verify_installed(live=False)
    if errors:
        raise RuntimeError("; ".join(errors))
    for vacuum in registry["vacuums"]:
        vacuum_id = vacuum["id"]
        validate_state_file(
            state_path(registry, vacuum_id), vacuum_id, require_inactive=True
        )
        snapshot = checkout / "controller" / f"{vacuum_id}-pause-snapshot.json"
        if snapshot.exists():
            raise ValueError(f"source upgrade requires no active snapshot: {snapshot}")
    changed = run_git(
        checkout, "diff", "--name-only", f"{current}..{target_commit}"
    ).stdout.splitlines()
    return {
        "planVersion": 1,
        "operation": "fast-forward-source-upgrade",
        "appliesChanges": False,
        "root": str(checkout),
        "currentCommit": current,
        "target": target,
        "targetCommit": target_commit,
        "hasChanges": current != target_commit,
        "changedFiles": changed,
        "validationCommands": [
            "python3 -m py_compile controller/*.py *.py",
            "python3 -m unittest discover -s tests -v",
            "bash -n ./*.sh",
            "python3 install-roborock-pause.py verify",
        ],
    }


def validate_source_checkout(root):
    """Run deterministic post-upgrade validation without a shell."""
    commands = (
        [
            "python3",
            "-m",
            "py_compile",
            *sorted(str(path) for path in Path(root).glob("*.py")),
            *sorted(str(path) for path in (Path(root) / "controller").glob("*.py")),
        ],
        ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
    )
    for command in commands:
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode:
            raise RuntimeError(f"post-upgrade validation failed: {' '.join(command)}")
    shell_files = sorted(Path(root).glob("*.sh")) + sorted(
        (Path(root) / "tools" / "live").glob("*.sh")
    )
    for path in shell_files:
        completed = subprocess.run(["bash", "-n", str(path)], cwd=root, check=False)
        if completed.returncode:
            raise RuntimeError(f"post-upgrade shell validation failed: {path}")
    completed = subprocess.run(
        ["python3", "install-roborock-pause.py", "verify"],
        cwd=root,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("post-upgrade local installation verification failed")


def apply_source_upgrade(target, root=None, validator=validate_source_checkout):
    """Fast-forward tracked source and roll it back if validation fails."""
    initial = source_upgrade_plan(target, root)
    checkout = Path(initial["root"])
    registry = load_registry()
    if not initial["hasChanges"]:
        return {"plan": initial, "rolledBack": False}
    with migration_locks(registry, registry):
        plan = source_upgrade_plan(target, checkout)
        try:
            run_git(checkout, "merge", "--ff-only", plan["targetCommit"])
            validator(checkout)
        except Exception:
            run_git(checkout, "reset", "--hard", plan["currentCommit"])
            raise
    return {"plan": plan, "rolledBack": False}


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="validate prerequisites")
    check.add_argument("--live", action="store_true", help="perform read-only discovery")

    discover = subparsers.add_parser("discover", help="print a registry proposal")
    discover.add_argument("--input", help="offline accessories JSON")

    stage_parser = subparsers.add_parser("stage", help="create a new staging directory")
    stage_parser.add_argument("--output", required=True)
    stage_source = stage_parser.add_mutually_exclusive_group()
    stage_source.add_argument("--input", help="offline accessories JSON")
    stage_source.add_argument(
        "--registry", help="validated registry JSON for offline migration planning"
    )

    show = subparsers.add_parser("show", help="show a validated stage")
    show.add_argument("--stage", required=True)

    install = subparsers.add_parser("install", help="install a validated stage")
    install.add_argument("--stage", required=True)
    install.add_argument("--systemd-dir")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--yes", action="store_true")

    verify = subparsers.add_parser("verify", help="verify an installation")
    verify.add_argument("--live", action="store_true", help="perform read-only discovery")

    uninstall = subparsers.add_parser("uninstall", help="remove installer-owned config")
    uninstall.add_argument("--systemd-dir")
    uninstall.add_argument("--dry-run", action="store_true")
    uninstall.add_argument("--yes", action="store_true")

    migrate = subparsers.add_parser(
        "migrate-plan", help="print a no-write unified manifest migration plan"
    )
    migrate.add_argument("--stage", required=True)
    migrate_apply = subparsers.add_parser(
        "migrate-apply", help="apply a guarded unified manifest migration"
    )
    migrate_apply.add_argument("--stage", required=True)
    migrate_apply.add_argument("--dry-run", action="store_true")
    migrate_apply.add_argument("--yes", action="store_true")
    reconfigure = subparsers.add_parser(
        "reconfigure-plan", help="print a no-write managed registry change plan"
    )
    reconfigure.add_argument("--stage", required=True)
    reconfigure_apply = subparsers.add_parser(
        "reconfigure-apply", help="apply a guarded managed registry change"
    )
    reconfigure_apply.add_argument("--stage", required=True)
    reconfigure_apply.add_argument("--dry-run", action="store_true")
    reconfigure_apply.add_argument("--yes", action="store_true")
    upgrade = subparsers.add_parser(
        "upgrade-plan", help="plan an upgrade to a previously fetched Git target"
    )
    upgrade.add_argument("--target", default="origin/master")
    upgrade_apply = subparsers.add_parser(
        "upgrade-apply", help="apply and validate a fast-forward source upgrade"
    )
    upgrade_apply.add_argument("--target", default="origin/master")
    upgrade_apply.add_argument("--dry-run", action="store_true")
    upgrade_apply.add_argument("--yes", action="store_true")
    return parser.parse_args(arguments)


def main(arguments=None):
    args = parse_args(arguments)
    try:
        if args.command == "check":
            registry = load_registry()
            errors = check_runtime(registry)
            if args.live:
                accessories = discovery.load_accessories(registry=registry)
                errors.extend(
                    discovery.validate_registry_against_accessories(
                        registry, accessories
                    )
                )
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            print("PASS: installation prerequisites validated.")
            return 0

        if args.command == "discover":
            registry, _ = discover_registry(args.input)
            sys.stdout.buffer.write(json_bytes(registry))
            return 0

        if args.command == "stage":
            path = stage(args.output, args.input, args.registry)
            print(f"PASS: staged installation at {path}")
            return 0

        if args.command == "show":
            manifest, _ = validate_stage(args.stage)
            print(json.dumps(manifest, indent=2))
            for relative in sorted(manifest["artifacts"]):
                print(f"\n===== {relative} =====")
                print((Path(args.stage) / relative).read_text(encoding="utf-8"), end="")
            return 0

        if args.command == "install":
            _, _, actions = install_plan(args.stage, args.systemd_dir)
            ensure_targets_absent(actions)
            print_actions(actions, "INSTALL PLAN")
            if args.dry_run:
                print("DRY RUN: no files were changed.")
                return 0
            if not args.yes:
                print("ERROR: installation requires --yes", file=sys.stderr)
                return 2
            apply_install(args.stage, args.systemd_dir)
            print("PASS: staged files installed; systemctl was not invoked.")
            return 0

        if args.command == "verify":
            errors = verify_installed(args.live)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            print("PASS: installation validated.")
            return 0

        if args.command == "uninstall":
            actions = uninstall_plan(args.systemd_dir)
            print_actions(actions, "UNINSTALL PLAN (state files are preserved)")
            if args.dry_run:
                print("DRY RUN: no files were changed.")
                return 0
            if not args.yes:
                print("ERROR: uninstall requires --yes", file=sys.stderr)
                return 2
            apply_uninstall(actions)
            print("PASS: installer-owned configuration removed; state files preserved.")
            return 0

        if args.command == "migrate-plan":
            plan = unified_migration_plan(args.stage)
            print(json.dumps(plan, indent=2))
            print("DRY RUN: migration planning changed no files.")
            return 0
        if args.command == "migrate-apply":
            plan = unified_migration_plan(args.stage)
            print(json.dumps(plan, indent=2))
            if args.dry_run:
                print("DRY RUN: no files were changed.")
                return 0
            if not args.yes:
                print("ERROR: migration requires --yes", file=sys.stderr)
                return 2
            result = apply_unified_migration(args.stage)
            print("PASS: unified manifest migration applied and verified.")
            if result["archive"]:
                print(f"Removed-vacuum archive: {result['archive']}")
            print("Homebridge, Roborock, and systemctl were not invoked.")
            return 0
        if args.command == "reconfigure-plan":
            plan = unified_reconfiguration_plan(args.stage)
            print(json.dumps(plan, indent=2))
            print("DRY RUN: reconfiguration planning changed no files.")
            return 0
        if args.command == "reconfigure-apply":
            plan = unified_reconfiguration_plan(args.stage)
            print(json.dumps(plan, indent=2))
            if args.dry_run:
                print("DRY RUN: no files were changed.")
                return 0
            if not args.yes:
                print("ERROR: reconfiguration requires --yes", file=sys.stderr)
                return 2
            if not plan["hasChanges"]:
                print("PASS: proposed registry is unchanged; nothing was written.")
                return 0
            result = apply_unified_reconfiguration(args.stage)
            print("PASS: managed registry reconfiguration applied and verified.")
            if result["archive"]:
                print(f"Removed-vacuum archive: {result['archive']}")
            print("Homebridge, Roborock, and systemctl were not invoked.")
            return 0
        if args.command == "upgrade-plan":
            plan = source_upgrade_plan(args.target)
            print(json.dumps(plan, indent=2))
            print("DRY RUN: source upgrade planning changed no files.")
            return 0
        if args.command == "upgrade-apply":
            plan = source_upgrade_plan(args.target)
            print(json.dumps(plan, indent=2))
            if args.dry_run:
                print("DRY RUN: no tracked source file was changed.")
                return 0
            if not args.yes:
                print("ERROR: source upgrade requires --yes", file=sys.stderr)
                return 2
            if not plan["hasChanges"]:
                print("PASS: source is already at the requested target.")
                return 0
            apply_source_upgrade(args.target)
            print("PASS: source fast-forwarded and deterministic validation passed.")
            print("Runtime configuration, Homebridge, Roborock, and systemctl were untouched.")
            return 0
    except (FileExistsError, OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
