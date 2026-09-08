import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "setup-homebridge-auto-auth.sh"


class AutomaticAuthSetupTests(unittest.TestCase):
    def test_credentials_use_shell_prompts_and_protected_temporary_files(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('read -r -p "Homebridge automation username:', source)
        self.assertIn('read -r -s -p "Homebridge automation password:', source)
        self.assertIn('printf \'%s\' "$HB_USER" >"$username_temp"', source)
        self.assertIn('printf \'%s\' "$HB_PASS" >"$password_temp"', source)
        self.assertIn("unset HB_USER HB_PASS", source)
        self.assertIn('chmod 600 "$credentials_temp"', source)
        self.assertIn("username_path.read_text", source)
        self.assertIn("password_path.read_text", source)
        self.assertNotIn('input("Homebridge automation username:', source)
        self.assertNotIn('open("/dev/tty"', source)


if __name__ == "__main__":
    unittest.main()
