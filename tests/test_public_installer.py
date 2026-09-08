import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "controller" / "public_installer.py"
SPEC = importlib.util.spec_from_file_location("public_installer_tests", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def service(name, characteristic_type, writable=False):
    permissions = ["pr", "ev"] + (["pw"] if writable else [])
    return {
        "serviceName": name,
        "uniqueId": f"fixture/{name}",
        "serviceCharacteristics": [
            {
                "type": characteristic_type,
                "value": False if characteristic_type == "On" else 1,
                "perms": permissions,
            }
        ],
    }


def accessories():
    return [
        service("Main Floor Rock Schedule 1", "On", True),
        service("Main Floor Rock Schedule 2", "On", True),
        service("Main Floor Rock Cleaning", "ContactSensorState"),
        service("Main Floor Rock Docked", "ContactSensorState"),
        service("Main Floor Rock Return to Dock", "On", True),
    ]




class PublicInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "runtime"
        self.fixture = self.base / "accessories.json"
        self.fixture.write_text(json.dumps(accessories()), encoding="utf-8")
        self.environment = mock.patch.dict(
            os.environ,
            {
                "ROBOROCK_PAUSE_ROOT": str(self.root),
                "ROBOROCK_PAUSE_CONFIG_FILE": str(
                    self.root / "controller" / "vacuums.json"
                ),
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def create_runtime(self):
        for path in MODULE.required_runtime_files(self.root):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)

    def create_stage(self):
        stage_path = self.base / "stage"
        MODULE.stage(stage_path, self.fixture)
        return stage_path


    def rewrite_staged_registry(self, stage_path, vacuums):
        registry_path = stage_path / MODULE.REGISTRY_NAME
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["vacuums"] = vacuums
        registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        manifest_path = stage_path / MODULE.STAGE_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][MODULE.REGISTRY_NAME] = MODULE.sha256(registry_path)
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def create_existing_states(self, active_vacuum=None):
        for vacuum_id in ("downtown", "uptown"):
            path = self.root / "controller" / f"{vacuum_id}-pause-state.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "vacuumId": vacuum_id,
                        "pauseActive": vacuum_id == active_vacuum,
                        "sessionId": f"existing-{vacuum_id}",
                    }
                ),
                encoding="utf-8",
            )

    def test_stage_is_private_complete_and_tamper_evident(self):
        stage_path = self.create_stage()
        manifest, registry = MODULE.validate_stage(stage_path)
        self.assertEqual(stat.S_IMODE(stage_path.stat().st_mode), 0o700)
        self.assertEqual(registry["vacuums"][0]["id"], "main-floor")
        self.assertEqual(
            set(manifest["artifacts"]),
            {
                "vacuums.json",
                "command-switches.json",
                "systemd/roborock-pause-until-tomorrow.service",
                "systemd/roborock-pause-until-tomorrow.timer",
            },
        )
        commands = json.loads((stage_path / "command-switches.json").read_text())
        self.assertIn("main-floor state", commands["switches"][0]["state"])

        (stage_path / "command-switches.json").write_text("modified")
        with self.assertRaisesRegex(ValueError, "missing or modified"):
            MODULE.validate_stage(stage_path)

    def test_stage_refuses_to_replace_directory(self):
        stage_path = self.base / "stage"
        stage_path.mkdir()
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            MODULE.stage(stage_path, self.fixture)

    def test_stage_accepts_registry_without_accessory_discovery(self):
        registry = self.base / "proposed-vacuums.json"
        registry.write_text(
            json.dumps(
                {
                    "version": 1,
                    "root": str(self.root),
                    "vacuums": [
                        {
                            "id": "planned",
                            "displayName": "Planned",
                            "scheduleNamePrefix": "Planned Schedule ",
                            "cleaningServiceName": "Planned Cleaning",
                            "dockedServiceName": "Planned Docked",
                            "returnToDockServiceName": "Planned Return",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(MODULE.discovery, "load_accessories") as discovery:
            stage_path = self.base / "registry-stage"
            MODULE.stage(stage_path, registry_path=registry)

        discovery.assert_not_called()
        _, staged = MODULE.validate_stage(stage_path)
        self.assertEqual(staged["vacuums"][0]["id"], "planned")

    def test_install_verify_and_uninstall_preserve_state(self):
        self.create_runtime()
        stage_path = self.create_stage()
        systemd = self.base / "systemd"
        actions = MODULE.apply_install(stage_path, systemd)
        self.assertTrue(actions)
        registry_path = self.root / "controller" / "vacuums.json"
        state_path = self.root / "controller" / "main-floor-pause-state.json"
        self.assertTrue(registry_path.is_file())
        self.assertEqual(stat.S_IMODE(registry_path.stat().st_mode), 0o644)
        self.assertFalse(json.loads(state_path.read_text())["pauseActive"])
        self.assertEqual(MODULE.verify_installed(), [])

        uninstall = MODULE.uninstall_plan(systemd)
        MODULE.apply_uninstall(uninstall)
        self.assertFalse(registry_path.exists())
        self.assertFalse((systemd / "roborock-pause-until-tomorrow.timer").exists())
        self.assertTrue(state_path.exists())

    def test_install_refuses_existing_target_and_rolls_back(self):
        self.create_runtime()
        stage_path = self.create_stage()
        registry_path = self.root / "controller" / "vacuums.json"
        registry_path.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "refusing to replace"):
            MODULE.apply_install(stage_path)
        self.assertEqual(registry_path.read_text(), "existing")
        self.assertFalse(
            (self.root / "controller" / "main-floor-pause-state.json").exists()
        )

    def test_uninstall_refuses_modified_owned_file(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        registry_path = self.root / "controller" / "vacuums.json"
        registry_path.write_text(registry_path.read_text() + "\n", encoding="utf-8")
        actions = MODULE.uninstall_plan()
        with self.assertRaisesRegex(RuntimeError, "modified file"):
            MODULE.apply_uninstall(actions)
        self.assertTrue(registry_path.exists())

    def test_cli_requires_explicit_confirmation(self):
        self.create_runtime()
        stage_path = self.create_stage()
        self.assertEqual(MODULE.main(["install", "--stage", str(stage_path)]), 2)
        self.assertFalse(
            (self.root / "controller" / "main-floor-pause-state.json").exists()
        )







    def test_unified_migration_plan_is_read_only_and_normalizes_manifest(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        tracked = {
            path: path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

        plan = MODULE.unified_migration_plan(stage_path)

        self.assertFalse(plan["appliesChanges"])
        self.assertEqual(plan["registryDiff"]["unchanged"], ["main-floor"])
        self.assertEqual(plan["registryDiff"]["added"], [])
        self.assertEqual(plan["registryDiff"]["removed"], [])
        self.assertEqual(plan["proposedManifest"]["version"], 2)
        self.assertEqual(
            plan["proposedManifest"]["installationMode"], "managed"
        )
        self.assertEqual(
            {path: path.read_bytes() for path in tracked}, tracked
        )


    def test_migrate_plan_cli_has_no_apply_option_and_changes_no_files(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        state_path = self.root / "controller" / "main-floor-pause-state.json"
        original_state = state_path.read_bytes()

        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(
                MODULE.main(["migrate-plan", "--stage", str(stage_path)]), 0
            )

        self.assertIn('"appliesChanges": false', output.getvalue())
        self.assertIn("DRY RUN", output.getvalue())
        self.assertEqual(state_path.read_bytes(), original_state)
        with self.assertRaises(SystemExit):
            MODULE.parse_args(
                ["migrate-plan", "--stage", str(stage_path), "--yes"]
            )

    def test_unified_manifest_schema_rejects_invalid_version(self):
        manifest = {
            "version": 1,
            "installationMode": "managed",
            "migratedFrom": {
                "version": 1,
                "installationMode": "adopted",
                "manifestPath": "/runtime/manifest.json",
            },
            "registry": {"path": "/runtime/vacuums.json", "proposedSha256": "a" * 64},
            "vacuumIds": ["downtown"],
            "managedFiles": [],
            "preservedFiles": [],
            "observedFiles": [],
        }

        with self.assertRaisesRegex(ValueError, "version"):
            MODULE.validate_unified_manifest(manifest)

    def test_registry_diff_reports_add_remove_and_selector_changes(self):
        current = {
            "vacuums": [
                {
                    "id": "old",
                    "displayName": "Old",
                    "scheduleNamePrefix": "Old Schedule ",
                    "cleaningServiceName": "Old Cleaning",
                    "dockedServiceName": "Old Docked",
                    "returnToDockServiceName": "Old Return",
                },
                {
                    "id": "same",
                    "displayName": "Same",
                    "scheduleNamePrefix": "Same Schedule ",
                    "cleaningServiceName": "Same Cleaning",
                    "dockedServiceName": "Same Docked",
                    "returnToDockServiceName": "Same Return",
                },
            ]
        }
        proposed = {
            "vacuums": [
                {**current["vacuums"][1], "displayName": "Renamed"},
                {**current["vacuums"][0], "id": "new"},
            ]
        }

        diff = MODULE.registry_vacuum_diff(current, proposed)

        self.assertEqual(diff["added"], ["new"])
        self.assertEqual(diff["removed"], ["old"])
        self.assertEqual(diff["changed"][0]["id"], "same")
        self.assertEqual(
            diff["changed"][0]["fields"]["displayName"],
            {"from": "Same", "to": "Renamed"},
        )

    def test_unified_migration_rejects_active_snapshot(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        snapshot = self.root / "controller" / "main-floor-pause-snapshot.json"
        snapshot.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "no active snapshot"):
            MODULE.unified_migration_plan(stage_path)

    def test_unified_migration_added_vacuum_requires_unused_state_path(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        existing = json.loads(
            (stage_path / MODULE.REGISTRY_NAME).read_text(encoding="utf-8")
        )["vacuums"][0]
        added = {**existing, "id": "added", "displayName": "Added"}
        self.rewrite_staged_registry(stage_path, [existing, added])
        added_state = self.root / "controller" / "added-pause-state.json"
        added_state.write_text("collision", encoding="utf-8")

        with self.assertRaisesRegex(FileExistsError, "already exists"):
            MODULE.unified_migration_plan(stage_path)

    def test_unified_migration_apply_replaces_manifest_and_verifies(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        registry_path = self.root / "controller" / "vacuums.json"
        state_path = self.root / "controller" / "main-floor-pause-state.json"
        original_registry = registry_path.read_bytes()
        original_state = state_path.read_bytes()

        previous_umask = os.umask(0o077)
        try:
            result = MODULE.apply_unified_migration(stage_path)
        finally:
            os.umask(previous_umask)

        manifest = json.loads(
            (self.root / "controller" / "installer-manifest.json").read_text()
        )
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["installationMode"], "managed")
        self.assertEqual(stat.S_IMODE(registry_path.stat().st_mode), 0o644)
        self.assertEqual(
            stat.S_IMODE(
                (self.root / "controller" / "installer-manifest.json").stat().st_mode
            ),
            0o600,
        )
        self.assertEqual(registry_path.read_bytes(), original_registry)
        self.assertEqual(state_path.read_bytes(), original_state)
        self.assertIsNone(result["archive"])
        self.assertEqual(MODULE.verify_installed(), [])
        self.assertEqual(MODULE.uninstall_plan()[0]["kind"], "registry")

    def test_unified_migration_apply_archives_removal_and_initializes_addition(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        current = json.loads((stage_path / MODULE.REGISTRY_NAME).read_text())
        replacement = {**current["vacuums"][0], "id": "replacement"}
        self.rewrite_staged_registry(stage_path, [replacement])
        old_state = self.root / "controller" / "main-floor-pause-state.json"
        old_contents = old_state.read_bytes()

        result = MODULE.apply_unified_migration(stage_path)

        archive = Path(result["archive"])
        self.assertFalse(old_state.exists())
        self.assertEqual(
            (archive / "main-floor" / old_state.name).read_bytes(), old_contents
        )
        added_state = self.root / "controller" / "replacement-pause-state.json"
        self.assertFalse(json.loads(added_state.read_text())["pauseActive"])
        self.assertEqual(MODULE.verify_installed(), [])

    def test_unified_migration_apply_rolls_back_every_local_change(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        current = json.loads((stage_path / MODULE.REGISTRY_NAME).read_text())
        replacement = {**current["vacuums"][0], "id": "replacement"}
        self.rewrite_staged_registry(stage_path, [replacement])
        registry_path = self.root / "controller" / "vacuums.json"
        manifest_path = self.root / "controller" / "installer-manifest.json"
        old_state = self.root / "controller" / "main-floor-pause-state.json"
        before = (
            registry_path.read_bytes(),
            manifest_path.read_bytes(),
            old_state.read_bytes(),
        )

        with mock.patch.object(
            MODULE,
            "verify_installed",
            side_effect=[[], ["simulated verification failure"]],
        ):
            with self.assertRaisesRegex(RuntimeError, "post-migration"):
                MODULE.apply_unified_migration(stage_path)

        self.assertEqual(registry_path.read_bytes(), before[0])
        self.assertEqual(manifest_path.read_bytes(), before[1])
        self.assertEqual(old_state.read_bytes(), before[2])
        self.assertFalse(
            (self.root / "controller" / "replacement-pause-state.json").exists()
        )
        self.assertEqual(
            list((self.root / "controller").glob("removed-vacuums-*")), []
        )

    def test_migrate_apply_cli_requires_confirmation_and_dry_run_is_safe(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        manifest_path = self.root / "controller" / "installer-manifest.json"
        original = manifest_path.read_bytes()

        self.assertEqual(
            MODULE.main(["migrate-apply", "--stage", str(stage_path), "--dry-run"]),
            0,
        )
        self.assertEqual(manifest_path.read_bytes(), original)
        self.assertEqual(
            MODULE.main(["migrate-apply", "--stage", str(stage_path)]), 2
        )
        self.assertEqual(manifest_path.read_bytes(), original)

    def create_managed_installation(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        MODULE.apply_unified_migration(stage_path)
        return stage_path

    def test_reconfiguration_plan_requires_v2_and_is_read_only(self):
        self.create_runtime()
        stage_path = self.create_stage()
        MODULE.apply_install(stage_path)
        with self.assertRaisesRegex(ValueError, "requires a version 2"):
            MODULE.unified_reconfiguration_plan(stage_path)

        MODULE.apply_unified_migration(stage_path)
        tracked = {
            path: path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        plan = MODULE.unified_reconfiguration_plan(stage_path)
        self.assertFalse(plan["hasChanges"])
        self.assertFalse(plan["appliesChanges"])
        self.assertEqual({path: path.read_bytes() for path in tracked}, tracked)

    def test_reconfiguration_apply_adds_changes_and_removes_vacuums(self):
        stage_path = self.create_managed_installation()
        current = json.loads((stage_path / MODULE.REGISTRY_NAME).read_text())
        replacement = {
            **current["vacuums"][0],
            "id": "replacement",
            "displayName": "Replacement",
        }
        self.rewrite_staged_registry(stage_path, [replacement])
        old_state = self.root / "controller" / "main-floor-pause-state.json"
        old_contents = old_state.read_bytes()

        plan = MODULE.unified_reconfiguration_plan(stage_path)
        self.assertEqual(plan["registryDiff"]["added"], ["replacement"])
        self.assertEqual(plan["registryDiff"]["removed"], ["main-floor"])
        result = MODULE.apply_unified_reconfiguration(stage_path)

        archive = Path(result["archive"])
        self.assertEqual(
            (archive / "main-floor" / old_state.name).read_bytes(), old_contents
        )
        self.assertFalse(old_state.exists())
        replacement_state = (
            self.root / "controller" / "replacement-pause-state.json"
        )
        self.assertFalse(json.loads(replacement_state.read_text())["pauseActive"])
        manifest = json.loads(
            (self.root / "controller" / MODULE.INSTALLED_MANIFEST).read_text()
        )
        self.assertEqual(manifest["vacuumIds"], ["replacement"])
        self.assertEqual(MODULE.verify_installed(), [])

    def test_reconfiguration_apply_rolls_back_on_verification_failure(self):
        stage_path = self.create_managed_installation()
        current = json.loads((stage_path / MODULE.REGISTRY_NAME).read_text())
        replacement = {**current["vacuums"][0], "id": "replacement"}
        self.rewrite_staged_registry(stage_path, [replacement])
        registry_path = self.root / "controller" / MODULE.REGISTRY_NAME
        manifest_path = self.root / "controller" / MODULE.INSTALLED_MANIFEST
        old_state = self.root / "controller" / "main-floor-pause-state.json"
        before = (
            registry_path.read_bytes(),
            manifest_path.read_bytes(),
            old_state.read_bytes(),
        )

        with mock.patch.object(
            MODULE,
            "verify_installed",
            side_effect=[[], ["simulated verification failure"]],
        ):
            with self.assertRaisesRegex(RuntimeError, "post-migration"):
                MODULE.apply_unified_reconfiguration(stage_path)

        self.assertEqual(registry_path.read_bytes(), before[0])
        self.assertEqual(manifest_path.read_bytes(), before[1])
        self.assertEqual(old_state.read_bytes(), before[2])
        self.assertFalse(
            (self.root / "controller" / "replacement-pause-state.json").exists()
        )

    def test_reconfiguration_detects_vacuum_order_change(self):
        stage_path = self.create_managed_installation()
        current = json.loads((stage_path / MODULE.REGISTRY_NAME).read_text())
        first = current["vacuums"][0]
        second = {**first, "id": "second", "displayName": "Second"}
        self.rewrite_staged_registry(stage_path, [first, second])
        MODULE.apply_unified_reconfiguration(stage_path)

        self.rewrite_staged_registry(stage_path, [second, first])
        plan = MODULE.unified_reconfiguration_plan(stage_path)

        self.assertTrue(plan["hasChanges"])
        self.assertTrue(plan["orderChanged"])
        self.assertEqual(plan["registryDiff"]["changed"], [])

    def test_reconfigure_cli_requires_confirmation_and_noop_writes_nothing(self):
        stage_path = self.create_managed_installation()
        manifest_path = self.root / "controller" / MODULE.INSTALLED_MANIFEST
        original = manifest_path.read_bytes()
        arguments = ["reconfigure-apply", "--stage", str(stage_path)]

        self.assertEqual(MODULE.main([*arguments, "--dry-run"]), 0)
        self.assertEqual(MODULE.main(arguments), 2)
        self.assertEqual(MODULE.main([*arguments, "--yes"]), 0)
        self.assertEqual(manifest_path.read_bytes(), original)

    def test_verify_v2_rejects_manifest_registry_mismatch(self):
        self.create_managed_installation()
        manifest_path = self.root / "controller" / MODULE.INSTALLED_MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest["vacuumIds"] = ["wrong"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self.assertIn(
            "unified manifest vacuum IDs do not match registry",
            MODULE.verify_installed(),
        )

    def create_upgrade_repository(self):
        self.create_managed_installation()

        def git(*arguments):
            return subprocess.run(
                ["git", *arguments],
                cwd=self.root,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()

        git("init", "-q")
        git("config", "user.name", "Installer Tests")
        git("config", "user.email", "installer@example.invalid")
        git("add", ".")
        git("commit", "-qm", "base")
        base = git("rev-parse", "HEAD")
        source = self.root / "controller" / "schedule_pause_controller.py"
        source.write_text(source.read_text() + "# upgraded\n", encoding="utf-8")
        git("add", str(source.relative_to(self.root)))
        git("commit", "-qm", "upgrade")
        target = git("rev-parse", "HEAD")
        git("reset", "--hard", base)
        return base, target, source

    def test_source_upgrade_plan_requires_clean_fast_forward(self):
        base, target, source = self.create_upgrade_repository()

        plan = MODULE.source_upgrade_plan(target, self.root)

        self.assertEqual(plan["currentCommit"], base)
        self.assertEqual(plan["targetCommit"], target)
        self.assertTrue(plan["hasChanges"])
        self.assertIn(str(source.relative_to(self.root)), plan["changedFiles"])
        source.write_text(source.read_text() + "dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "clean tracked checkout"):
            MODULE.source_upgrade_plan(target, self.root)

    def test_source_upgrade_apply_fast_forwards_and_validates(self):
        _, target, source = self.create_upgrade_repository()
        validated = []

        result = MODULE.apply_source_upgrade(
            target, self.root, validator=lambda root: validated.append(root)
        )

        self.assertEqual(
            MODULE.run_git(self.root, "rev-parse", "HEAD").stdout.strip(), target
        )
        self.assertEqual(validated, [self.root.resolve()])
        self.assertIn("# upgraded", source.read_text())
        self.assertFalse(result["rolledBack"])

    def test_source_upgrade_rolls_back_failed_validation(self):
        base, target, source = self.create_upgrade_repository()

        def fail(_root):
            raise RuntimeError("simulated validation failure")

        with self.assertRaisesRegex(RuntimeError, "simulated validation"):
            MODULE.apply_source_upgrade(target, self.root, validator=fail)

        self.assertEqual(
            MODULE.run_git(self.root, "rev-parse", "HEAD").stdout.strip(), base
        )
        self.assertNotIn("# upgraded", source.read_text())
        self.assertEqual(MODULE.verify_installed(), [])

    def test_source_upgrade_cli_confirmation_and_noop(self):
        base, _, _ = self.create_upgrade_repository()
        arguments = ["upgrade-apply", "--target", base]

        self.assertEqual(MODULE.main([*arguments, "--dry-run"]), 0)
        self.assertEqual(MODULE.main(arguments), 2)
        self.assertEqual(MODULE.main([*arguments, "--yes"]), 0)


if __name__ == "__main__":
    unittest.main()
