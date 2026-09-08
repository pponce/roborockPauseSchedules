import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class DocumentationCloseoutTests(unittest.TestCase):
    def test_readme_links_to_closeout_documents(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for relative_path in (
            "docs/UPGRADE.md",
            "docs/RELEASE_CHECKLIST.md",
            "docs/MANIFEST_V2_MIGRATION.md",
            "docs/RECONFIGURE.md",
            "docs/MANAGED_UPGRADE.md",
            "docs/RELEASING.md",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertIn(f"]({relative_path})", readme)
                self.assertTrue((ROOT / relative_path).is_file())

    def test_upgrade_procedure_uses_existing_cli_contract(self):
        upgrade = (ROOT / "docs" / "UPGRADE.md").read_text(encoding="utf-8")
        self.assertIn("install-roborock-pause.py upgrade-plan", upgrade)
        self.assertIn("install-roborock-pause.py upgrade-apply", upgrade)
        self.assertIn("--target origin/master --dry-run", upgrade)
        self.assertIn("--target origin/master --yes", upgrade)
        self.assertIn("Run `verify --live` separately", upgrade)

    def test_release_checklist_preserves_live_write_boundary(self):
        checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("No live write is performed merely", checklist)
        self.assertIn("no historical live-write tools", checklist)
        self.assertIn("mode-`0600` recovery snapshot", checklist)

    def test_readme_has_beginner_install_and_reconfiguration_guides(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("You do **not** need to fork", readme)
        self.assertIn("Option A: download a ZIP", readme)
        self.assertIn("Fresh install, step by step", readme)
        self.assertIn("## Add a vacuum", readme)
        self.assertIn("## Remove a vacuum", readme)
        self.assertIn("## Script and file guide", readme)


if __name__ == "__main__":
    unittest.main()
