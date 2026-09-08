import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "controller" / "vacuum_registry.py"
ENGINE_PATH = ROOT / "controller" / "schedule_pause_controller.py"
CLI_PATH = ROOT / "controller" / "vacuum-pause-controller.py"

SAMPLE_VACUUM = {
    "id": "main-floor",
    "displayName": "Main Floor",
    "scheduleNamePrefix": "Main Schedule ",
    "cleaningServiceName": "Main Cleaning",
    "dockedServiceName": "Main Docked",
    "returnToDockServiceName": "Main Return to Dock",
}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRY = load_module("vacuum_registry_tests", REGISTRY_PATH)
ENGINE = load_module("schedule_pause_controller_tests", ENGINE_PATH)


class VacuumRegistryTests(unittest.TestCase):
    def test_missing_registry_fails_closed(self):
        with mock.patch.dict(
            os.environ, {"ROBOROCK_PAUSE_CONFIG_FILE": "/missing"}
        ):
            with self.assertRaisesRegex(FileNotFoundError, "registry does not exist"):
                REGISTRY.load_registry()

    def test_external_registry_supports_arbitrary_vacuum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vacuums.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": "/opt/roborock-pause",
                        "vacuums": [
                            {
                                "id": "main-floor",
                                "displayName": "Main Floor",
                                "scheduleNamePrefix": "Main Schedule ",
                                "cleaningServiceName": "Main Cleaning",
                                "dockedServiceName": "Main Docked",
                                "returnToDockServiceName": "Main Return to Dock",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ, {"ROBOROCK_PAUSE_CONFIG_FILE": str(path)}
            ):
                registry = REGISTRY.load_registry()

        config = REGISTRY.engine_config(ENGINE, registry, "main-floor")
        self.assertEqual(config.display_name, "Main Floor")
        self.assertEqual(
            config.snapshot_file,
            "/opt/roborock-pause/controller/main-floor-pause-snapshot.json",
        )
        vacuum = REGISTRY.orchestrator_vacuums(registry)[0]
        self.assertEqual(
            vacuum["onCommand"],
            [
                "python3",
                "/opt/roborock-pause/controller/vacuum-pause-controller.py",
                "main-floor",
                "on",
            ],
        )

    def test_duplicate_and_unsafe_ids_are_rejected(self):
        vacuum = dict(SAMPLE_VACUUM)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            REGISTRY.validate_vacuums([vacuum, vacuum])
        with self.assertRaisesRegex(ValueError, "filesystem-safe"):
            REGISTRY.validate_vacuums([{**vacuum, "id": "Not Safe"}])

    def test_installation_settings_support_config_and_environment(self):
        config = {
            "version": 1,
            "root": "/opt/pause",
            "homebridge": {
                "baseUrl": "http://homebridge.local:9999/",
                "tokenFile": "/run/private/homebridge-token",
            },
            "systemd": {"user": "hb", "group": "automation"},
            "vacuums": [dict(SAMPLE_VACUUM)],
        }
        with mock.patch.dict(
            os.environ,
            {
                "ROBOROCK_PAUSE_CREDENTIALS_FILE": "/run/private/credentials.json",
                "ROBOROCK_PAUSE_ON_CALENDAR": "*-*-* 00:15:00",
            },
        ):
            registry = REGISTRY.validate_registry_document(config)

        self.assertEqual(registry["root"], Path("/opt/pause"))
        self.assertEqual(
            registry["homebridge"]["baseUrl"], "http://homebridge.local:9999"
        )
        self.assertEqual(
            registry["homebridge"]["tokenFile"], "/run/private/homebridge-token"
        )
        self.assertEqual(
            registry["homebridge"]["credentialsFile"],
            "/run/private/credentials.json",
        )
        self.assertEqual(registry["systemd"]["user"], "hb")
        self.assertEqual(registry["systemd"]["onCalendar"], "*-*-* 00:15:00")

    def test_invalid_installation_settings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTP"):
            REGISTRY.validate_homebridge({"baseUrl": "localhost:8581"}, Path("/tmp"))
        with self.assertRaisesRegex(ValueError, "absolute"):
            REGISTRY.validate_homebridge({"tokenFile": "relative"}, Path("/tmp"))
        with self.assertRaisesRegex(ValueError, "bashPath"):
            REGISTRY.validate_systemd({"bashPath": "bash"})
        with self.assertRaisesRegex(ValueError, "unknown vacuum configuration"):
            REGISTRY.validate_registry_document(
                {
                    "version": 1,
                    "vacuums": [dict(SAMPLE_VACUUM)],
                    "typo": True,
                }
            )

    def test_generic_cli_rejects_unknown_vacuum(self):
        cli = load_module("vacuum_pause_cli_tests", CLI_PATH)
        registry = REGISTRY.validate_registry_document(
            {"version": 1, "vacuums": [dict(SAMPLE_VACUUM)]}
        )
        with mock.patch.object(
            cli, "load_registry", return_value=registry
        ), mock.patch.object(
            cli.sys, "argv", [str(CLI_PATH), "unknown", "state"]
        ), mock.patch(
            "sys.stderr", new_callable=io.StringIO
        ) as error:
            self.assertEqual(cli.main(), 2)
        self.assertIn("unknown vacuum ID", error.getvalue())

    def test_generic_cli_initializes_and_reads_configured_vacuum(self):
        cli = load_module("vacuum_pause_cli_state_tests", CLI_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "install"
            config_path = Path(directory) / "vacuums.json"
            config_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "root": str(root),
                        "homebridge": {
                            "baseUrl": "http://homebridge.test:9999",
                            "legacyTokenFile": str(root / "legacy-token"),
                            "tokenFile": str(root / "token"),
                            "credentialsFile": str(root / "credentials.json"),
                        },
                        "vacuums": [
                            {
                                "id": "main-floor",
                                "displayName": "Main Floor",
                                "scheduleNamePrefix": "Main Schedule ",
                                "cleaningServiceName": "Main Cleaning",
                                "dockedServiceName": "Main Docked",
                                "returnToDockServiceName": "Main Return to Dock",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            environment = {"ROBOROCK_PAUSE_CONFIG_FILE": str(config_path)}
            with mock.patch.dict(os.environ, environment), mock.patch.object(
                cli.sys, "argv", [str(CLI_PATH), "main-floor", "init"]
            ), mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(cli.main(), 0)

            with mock.patch.dict(os.environ, environment), mock.patch.object(
                cli.sys, "argv", [str(CLI_PATH), "main-floor", "state"]
            ), mock.patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(cli.main(), 0)

            self.assertEqual(output.getvalue(), "false\n")
            self.assertEqual(cli.engine.BASE_URL, "http://homebridge.test:9999")
            self.assertEqual(cli.engine.AUTO_AUTH_TOKEN_FILE, str(root / "token"))
            state_path = root / "controller" / "main-floor-pause-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["vacuumId"], "main-floor")
            self.assertFalse(state["pauseActive"])


if __name__ == "__main__":
    unittest.main()
