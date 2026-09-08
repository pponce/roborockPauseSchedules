import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "controller" / "discover_vacuum_config.py"
SPEC = importlib.util.spec_from_file_location("discover_vacuum_config_tests", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def service(name, characteristic_type, *, unique_id=None, writable=False):
    permissions = ["pr", "ev"]
    if writable:
        permissions.append("pw")
    characteristics = [
        {
            "type": characteristic_type,
            "value": False if characteristic_type == "On" else 1,
            "perms": permissions,
        }
    ]
    if characteristic_type == "On" and "Schedule" in name:
        characteristics.append(
            {"type": "ConfiguredName", "value": "Private display label", "perms": ["pr"]}
        )
    return {
        "serviceName": name,
        "uniqueId": unique_id or f"fixture/{name}",
        "serviceCharacteristics": characteristics,
    }


def vacuum_accessories(base="Main Floor Rock", schedule_count=2):
    accessories = [
        service(f"{base} Schedule {number}", "On", writable=True)
        for number in range(1, schedule_count + 1)
    ]
    accessories.extend(
        [
            service(f"{base} Cleaning", "ContactSensorState"),
            service(f"{base} Docked", "ContactSensorState"),
            service(f"{base} Return to Dock", "On", writable=True),
        ]
    )
    return accessories


class DiscoveryConfigTests(unittest.TestCase):
    def test_discovers_complete_candidate_and_proposal(self):
        candidates = MODULE.discover_candidates(vacuum_accessories())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["issues"], [])
        self.assertEqual(candidates[0]["entry"]["id"], "main-floor")
        self.assertEqual(candidates[0]["scheduleCount"], 2)

        proposal = MODULE.proposal_from_candidates(candidates)
        self.assertEqual(proposal["version"], 1)
        self.assertEqual(
            proposal["vacuums"][0]["scheduleNamePrefix"],
            "Main Floor Rock Schedule ",
        )
        self.assertNotIn("uniqueId", proposal["vacuums"][0])

    def test_incomplete_candidate_is_not_proposed(self):
        accessories = vacuum_accessories()
        accessories = [
            accessory
            for accessory in accessories
            if accessory["serviceName"] != "Main Floor Rock Docked"
        ]
        candidates = MODULE.discover_candidates(accessories)
        self.assertIn("found 0", candidates[0]["issues"][0])
        with self.assertRaisesRegex(ValueError, "no complete"):
            MODULE.proposal_from_candidates(candidates)

    def test_validation_detects_missing_and_nonwritable_services(self):
        accessories = vacuum_accessories()
        action = next(
            item for item in accessories if item["serviceName"].endswith("Return to Dock")
        )
        action["serviceCharacteristics"][0]["perms"].remove("pw")
        registry = {
            "vacuums": MODULE.validate_vacuums(
                [MODULE.discover_candidates(vacuum_accessories())[0]["entry"]]
            )
        }
        errors = MODULE.validate_registry_against_accessories(registry, accessories)
        self.assertEqual(len(errors), 1)
        self.assertIn("not writable", errors[0])

    def test_output_refuses_to_overwrite_existing_file(self):
        proposal = MODULE.proposal_from_candidates(
            MODULE.discover_candidates(vacuum_accessories())
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "vacuums.json"
            output.write_text("original", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                MODULE.write_proposal(proposal, output)
            self.assertEqual(output.read_text(encoding="utf-8"), "original")

    def test_live_discovery_calls_get_accessories_only(self):
        with mock.patch.object(
            MODULE.engine,
            "api_request",
            return_value=(200, vacuum_accessories()),
        ) as api_request, mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            self.assertEqual(MODULE.main([]), 0)
        api_request.assert_called_once_with("GET", "/api/accessories")
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["vacuums"][0]["id"], "main-floor")
        self.assertEqual(payload["homebridge"]["baseUrl"], "http://127.0.0.1:8581")
        self.assertEqual(payload["systemd"]["user"], "homebridge")

    def test_unrelated_schedule_service_is_ignored(self):
        accessories = vacuum_accessories()
        accessories.append(service("Calendar Schedule Status", "On", writable=True))
        candidates = MODULE.discover_candidates(accessories)
        self.assertEqual(
            [candidate["base"] for candidate in candidates], ["Main Floor Rock"]
        )

    def test_report_is_explicit_about_local_identifiers(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            MODULE.print_report(MODULE.discover_candidates(vacuum_accessories()))
        self.assertIn("ConfiguredName='Private display label'", output.getvalue())
        self.assertIn("uniqueId='fixture/", output.getvalue())


if __name__ == "__main__":
    unittest.main()
