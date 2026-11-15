"""Check restoration without touching the real user's MyPaint profile."""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "personal-profile"
SPEC = importlib.util.spec_from_file_location("restore_profile", ROOT / "tools/restore_profile.py")
RESTORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESTORE)


class RestoreProfileTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config = self.root / "config"
        self.data = self.root / "data"

    def test_preview_has_no_side_effects(self):
        result = RESTORE.restore(PROFILE, self.config, self.data)
        self.assertEqual(result["files"], 442)
        self.assertFalse(result["applied"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_clean_restore_preserves_all_files_and_relocates_settings(self):
        result = RESTORE.restore(PROFILE, self.config, self.data, apply=True)
        self.assertTrue(result["applied"])
        self.assertIsNone(result["backup"])
        manifest = json.loads((PROFILE / "manifest.json").read_text())
        for name, expected_hash in manifest["files"].items():
            bucket, relative = name.split("/", 1)
            restored = (self.config if bucket == "config" else self.data) / relative
            if name == "config/settings.json":
                expected = json.loads((PROFILE / name).read_text())
                expected["scratchpad.last_opened_scratchpad"] = str(
                    self.data / "scratchpads/autosave.ora"
                )
                self.assertEqual(json.loads(restored.read_text()), expected)
            else:
                self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(), expected_hash, name)
        self.assertEqual(len(list((self.data / "brushes").rglob("*.myb"))), 217)
        self.assertFalse((self.data / "scratchpads").exists())

    def test_existing_files_are_backed_up_and_drawings_are_untouched(self):
        self.config.mkdir()
        (self.config / "settings.json").write_bytes(b'old settings')
        (self.data / "brushes/classic").mkdir(parents=True)
        (self.data / "brushes/classic/pen.myb").write_bytes(b'old pen')
        (self.data / "brushes/additional.myb").write_bytes(b'extra brush')
        (self.data / "scratchpads").mkdir()
        (self.data / "scratchpads/autosave.ora").write_bytes(b'private drawing')
        result = RESTORE.restore(PROFILE, self.config, self.data, apply=True)
        backup = result["backup"]
        self.assertEqual((backup / "config/settings.json").read_bytes(), b'old settings')
        self.assertEqual((backup / "data/brushes/classic/pen.myb").read_bytes(), b'old pen')
        self.assertEqual((self.data / "brushes/additional.myb").read_bytes(), b'extra brush')
        self.assertEqual((self.data / "scratchpads/autosave.ora").read_bytes(), b'private drawing')
        self.assertNotEqual((self.data / "brushes/classic/pen.myb").read_bytes(), b'old pen')

    def test_corrupt_snapshot_cannot_modify_existing_settings(self):
        damaged = self.root / "damaged-profile"
        shutil.copytree(PROFILE, damaged)
        (damaged / "data/backgrounds/default.png").write_bytes(b'corrupt')
        self.config.mkdir()
        (self.config / "settings.json").write_bytes(b'keep settings')
        with self.assertRaisesRegex(ValueError, "контрольная сумма"):
            RESTORE.restore(damaged, self.config, self.data, apply=True)
        self.assertEqual((self.config / "settings.json").read_bytes(), b'keep settings')
        self.assertFalse(self.data.exists())

    def test_backup_failure_happens_before_any_settings_are_replaced(self):
        self.config.mkdir()
        (self.config / "settings.json").write_bytes(b'keep settings')
        with patch.object(RESTORE.shutil, "copy2", side_effect=OSError("backup unavailable")):
            with self.assertRaisesRegex(OSError, "backup unavailable"):
                RESTORE.restore(PROFILE, self.config, self.data, apply=True)
        self.assertEqual((self.config / "settings.json").read_bytes(), b'keep settings')
        self.assertFalse(self.data.exists())

    def test_destination_symlink_cannot_redirect_writes(self):
        self.config.mkdir()
        other = self.root / "elsewhere"
        other.write_bytes(b'keep other file')
        (self.config / "settings.json").symlink_to(other)
        with self.assertRaisesRegex(ValueError, "Ссылка"):
            RESTORE.restore(PROFILE, self.config, self.data, apply=True)
        self.assertEqual(other.read_bytes(), b'keep other file')
        self.assertFalse(self.data.exists())


if __name__ == "__main__":
    unittest.main()
