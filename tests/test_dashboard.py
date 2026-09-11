import tempfile
from pathlib import Path
import unittest

from gui import archive_person, list_people


class DashboardTests(unittest.TestCase):
    def test_archive_preserves_data_and_invalidates_model(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            folder = data / "faces" / "Eljah"
            folder.mkdir(parents=True)
            (folder / "one.png").write_bytes(b"capture")
            (data / "model.yml").write_text("old model")
            self.assertEqual(list_people(data), [("Eljah", 1)])
            archive = archive_person(data, "Eljah")
            self.assertEqual(list_people(data), [])
            self.assertFalse((data / "model.yml").exists())
            self.assertEqual((archive / "Eljah" / "one.png").read_bytes(), b"capture")
            self.assertEqual((archive / "model.yml").read_text(), "old model")

    def test_missing_person_does_not_invalidate_model(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "model.yml").write_text("keep")
            with self.assertRaises(RuntimeError):
                archive_person(data, "Absent")
            self.assertEqual((data / "model.yml").read_text(), "keep")
