import hashlib
import tempfile
import unittest
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = spec_from_file_location("build_release_zip", ROOT / "scripts" / "build-release-zip.py")
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseArchiveTests(unittest.TestCase):
    def test_archive_contains_runtime_and_excludes_private_and_developer_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, checksum = MODULE.build("1.2.3", Path(temporary))
            prefix = "roborockPauseSchedules-1.2.3/"
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
            self.assertIn(prefix + "README.md", names)
            self.assertIn(prefix + "controller/vacuum-pause-controller.py", names)
            self.assertIn(prefix + "config/vacuums.example.json", names)
            for relative in (
                "systemd/roborock-pause-reconcile.service",
                "systemd/roborock-pause-reconcile.timer",
                "systemd/roborock-pause-until-tomorrow.service",
                "systemd/roborock-pause-until-tomorrow.timer",
                "docs/ZIP_UPGRADE.md",
            ):
                self.assertIn(prefix + relative, names)
            self.assertFalse(any("tests/" in name for name in names))
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertNotIn(prefix + "controller/vacuums.json", names)
            controller_members = [
                name for name in names if name.startswith(prefix + "controller/")
            ]
            self.assertTrue(controller_members)
            self.assertTrue(all(name.endswith(".py") for name in controller_members))
            private_runtime_names = (
                "all-vacuums-pause-operation.log",
                "all-vacuums-pause.lock",
                "downtown-pause-state.json",
                "downtown-pause-snapshot.backup.json",
                "downtown-pause-snapshot.json.manual-recovery-example",
                "downtown-state-test.backup.json",
            )
            for name in private_runtime_names:
                with self.subTest(name=name):
                    self.assertNotIn(prefix + "controller/" + name, names)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(checksum.read_text(encoding="utf-8"), f"{digest}  {archive.name}\n")

    def test_invalid_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                MODULE.build("latest/unsafe", Path(temporary))


if __name__ == "__main__":
    unittest.main()
