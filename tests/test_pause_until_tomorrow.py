import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
STATE_MODULE_PATH = ROOT / "controller" / "pause_until_tomorrow.py"
CONTROLLER_PATH = ROOT / "controller" / "pause-until-tomorrow-controller.py"

STATE_SPEC = importlib.util.spec_from_file_location(
    "pause_until_tomorrow", STATE_MODULE_PATH
)
STATE_MODULE = importlib.util.module_from_spec(STATE_SPEC)
STATE_SPEC.loader.exec_module(STATE_MODULE)


class PauseUntilTomorrowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state_path = Path(self.directory.name) / "setting.json"
        self.environment = mock.patch.dict(
            os.environ,
            {"PAUSE_UNTIL_TOMORROW_STATE_FILE": str(self.state_path)},
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def load_controller(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(ROOT / "controller")}):
            spec = importlib.util.spec_from_file_location(
                "pause_until_tomorrow_controller", CONTROLLER_PATH
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    def test_missing_state_defaults_enabled(self):
        self.assertTrue(STATE_MODULE.read_enabled())

    def test_write_and_read_disabled_state(self):
        STATE_MODULE.write_enabled(False)
        self.assertFalse(STATE_MODULE.read_enabled())
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_invalid_state_is_rejected(self):
        self.state_path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "identity"):
            STATE_MODULE.read_enabled()

    def test_controller_state_outputs_default_true(self):
        controller = self.load_controller()
        with mock.patch.object(controller.sys, "argv", [str(CONTROLLER_PATH), "state"]), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            self.assertEqual(controller.main(), 0)
        self.assertEqual(output.getvalue(), "true\n")

    def test_controller_off_persists_disabled(self):
        controller = self.load_controller()
        with mock.patch.object(controller.sys, "argv", [str(CONTROLLER_PATH), "off"]):
            self.assertEqual(controller.main(), 0)
        self.assertFalse(STATE_MODULE.read_enabled())

    def test_default_state_path_follows_configured_root(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"ROBOROCK_PAUSE_ROOT": directory},
            clear=True,
        ):
            self.assertEqual(
                STATE_MODULE.state_file(),
                Path(directory) / "controller" / "pause-until-tomorrow-state.json",
            )


if __name__ == "__main__":
    unittest.main()
