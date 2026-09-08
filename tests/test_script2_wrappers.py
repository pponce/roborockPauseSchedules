import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
ALL_ON_SCRIPT = ROOT / "all-vacuums-pause-on.sh"
ALL_OFF_SCRIPT = ROOT / "all-vacuums-pause-off.sh"
ALL_STATE_SCRIPT = ROOT / "all-vacuums-pause-state.sh"
ALL_EXPIRE_SCRIPT = ROOT / "all-vacuums-pause-expire.sh"
UNTIL_TOMORROW_ON_SCRIPT = ROOT / "pause-until-tomorrow-on.sh"
UNTIL_TOMORROW_OFF_SCRIPT = ROOT / "pause-until-tomorrow-off.sh"
UNTIL_TOMORROW_STATE_SCRIPT = ROOT / "pause-until-tomorrow-state.sh"


class CommandWrapperTests(unittest.TestCase):
    def run_wrapper(self, script, *arguments, exit_status=0):
        with tempfile.TemporaryDirectory() as directory:
            fake_python = Path(directory) / "python3"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\"\n"
                f"exit {exit_status}\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{directory}:{environment['PATH']}"
            return subprocess.run(
                [str(script), *arguments],
                capture_output=True,
                check=False,
                text=True,
                env=environment,
            )

    def test_all_vacuums_wrappers_invoke_orchestrator(self):
        cases = (
            (ALL_ON_SCRIPT, "dispatch-on"),
            (ALL_OFF_SCRIPT, "dispatch-off"),
            (ALL_STATE_SCRIPT, "state"),
            (ALL_EXPIRE_SCRIPT, "expire"),
        )
        for script, command in cases:
            with self.subTest(command=command):
                result = self.run_wrapper(script, exit_status=19)
                lines = result.stdout.splitlines()
                self.assertEqual(result.returncode, 19)
                self.assertTrue(
                    lines[0].endswith("controller/all-vacuums-pause-controller.py")
                )
                self.assertEqual(lines[-1], command)

    def test_pause_until_tomorrow_wrappers_invoke_setting_controller(self):
        cases = (
            (UNTIL_TOMORROW_ON_SCRIPT, "on"),
            (UNTIL_TOMORROW_OFF_SCRIPT, "off"),
            (UNTIL_TOMORROW_STATE_SCRIPT, "state"),
        )
        for script, command in cases:
            with self.subTest(command=command):
                result = self.run_wrapper(script, exit_status=19)
                lines = result.stdout.splitlines()
                self.assertEqual(result.returncode, 19)
                self.assertTrue(
                    lines[0].endswith(
                        "controller/pause-until-tomorrow-controller.py"
                    )
                )
                self.assertEqual(lines[-1], command)


if __name__ == "__main__":
    unittest.main()
