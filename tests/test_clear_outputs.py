import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from clear_outputs import clear_generated_json


class ClearOutputsTests(unittest.TestCase):
    def test_deletes_generated_json_including_nested_assessments_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = ["data/output/job.json", "data/assessments/job.json", "data/assessments/evaluation/old.json",
                     "data/output/.gitkeep", "data/input/job.pdf", "local/evaluation/result.json"]
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            self.assertEqual(clear_generated_json(root), 3)
            for name in files[:3]:
                self.assertFalse((root / name).exists())
            for name in files[3:]:
                self.assertTrue((root / name).exists())
            self.assertTrue((root / "data/assessments/evaluation").is_dir())

    def test_missing_folders_are_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(clear_generated_json(Path(directory)), 0)
