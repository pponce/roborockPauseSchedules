import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "controller" / "render_systemd_units.py"
SPEC = importlib.util.spec_from_file_location("render_systemd_units_tests", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RenderSystemdUnitsTests(unittest.TestCase):
    def test_default_settings_match_packaged_unit_sources(self):
        registry = {
            "root": Path("/var/lib/homebridge/roborockPauseSchedules"),
            "systemd": {
                "user": "homebridge",
                "group": "homebridge",
                "bashPath": "/usr/bin/bash",
                "homebridgeUnit": "homebridge.service",
                "onCalendar": "*-*-* 00:05:00",
            },
        }
        units = MODULE.render_units(registry)
        self.assertEqual(
            units[MODULE.SERVICE_NAME],
            (ROOT / "systemd" / MODULE.SERVICE_NAME).read_text(encoding="utf-8"),
        )
        self.assertEqual(
            units[MODULE.TIMER_NAME],
            (ROOT / "systemd" / MODULE.TIMER_NAME).read_text(encoding="utf-8"),
        )

    def test_custom_settings_are_rendered_without_installing(self):
        registry = {
            "root": Path("/opt/roborock-pause"),
            "systemd": {
                "user": "automation",
                "group": "automation",
                "bashPath": "/bin/bash",
                "homebridgeUnit": "container-homebridge.service",
                "onCalendar": "*-*-* 00:30:00",
            },
        }
        units = MODULE.render_units(registry)
        service = units[MODULE.SERVICE_NAME]
        timer = units[MODULE.TIMER_NAME]
        self.assertIn("User=automation", service)
        self.assertIn("WorkingDirectory=/opt/roborock-pause", service)
        self.assertIn(
            "After=network-online.target container-homebridge.service", service
        )
        self.assertIn("OnCalendar=*-*-* 00:30:00", timer)

    def test_writer_refuses_to_replace_existing_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            existing = output / MODULE.SERVICE_NAME
            existing.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                MODULE.write_units(
                    {MODULE.SERVICE_NAME: "replace", MODULE.TIMER_NAME: "timer"},
                    output,
                )
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
            self.assertFalse((output / MODULE.TIMER_NAME).exists())


if __name__ == "__main__":
    unittest.main()
