import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_pipeline


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "outputs"
        self.results = self.root / "results"
        self.inputs.mkdir()
        self.outputs.mkdir()
        self.results.mkdir()
        for name in ("Job with spaces.html", "Job.pdf"):
            (self.inputs / name).write_text("source fixture")
        for name in ("Job with spaces.json", "Job.pdf.json", "stale.json"):
            (self.outputs / name).write_text("{}")
        (self.results / "comparison.html").write_text("visual")
        self.argv = ["run_pipeline.py", "--extract-model", "extract-model", "--models", "first-model", "second-model",
                     "--input-dir", str(self.inputs), "--extracted-dir", str(self.outputs), "--output", str(self.results)]

    def test_full_workflow_keeps_separate_steps_and_excludes_stale_outputs(self):
        with patch.object(sys, "argv", self.argv + ["--force"]), patch("run_pipeline.subprocess.run") as run, patch("run_pipeline.webbrowser.open") as browser:
            run.return_value = SimpleNamespace(returncode=0)
            self.assertEqual(run_pipeline.main(), 0)
        self.assertEqual(run.call_count, 2)
        extraction = run.call_args_list[0].args[0]
        comparison = run.call_args_list[1].args[0]
        self.assertIn("extract-model", extraction)
        self.assertNotIn("--force", extraction)
        self.assertIn("--force", comparison)
        self.assertIn(str(self.outputs / "Job with spaces.json"), comparison)
        self.assertIn(str(self.outputs / "Job.pdf.json"), comparison)
        self.assertNotIn(str(self.outputs / "stale.json"), comparison)
        browser.assert_called_once()

    def test_extraction_force_is_separate_and_browser_can_be_disabled(self):
        with patch.object(sys, "argv", self.argv + ["--force-extraction", "--no-open"]), patch("run_pipeline.subprocess.run") as run, patch("run_pipeline.webbrowser.open") as browser:
            run.return_value = SimpleNamespace(returncode=0)
            self.assertEqual(run_pipeline.main(), 0)
        self.assertIn("--force", run.call_args_list[0].args[0])
        self.assertNotIn("--force", run.call_args_list[1].args[0])
        browser.assert_not_called()

    def test_extraction_failure_stops_before_comparison(self):
        with patch.object(sys, "argv", self.argv), patch("run_pipeline.subprocess.run") as run, patch("run_pipeline.webbrowser.open") as browser:
            run.return_value = SimpleNamespace(returncode=1)
            self.assertEqual(run_pipeline.main(), 1)
        self.assertEqual(run.call_count, 1)
        browser.assert_not_called()

    def test_missing_output_stops_before_comparison(self):
        (self.outputs / "Job.pdf.json").unlink()
        with patch.object(sys, "argv", self.argv), patch("run_pipeline.subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0)
            self.assertEqual(run_pipeline.main(), 1)
        self.assertEqual(run.call_count, 1)

    def test_comparison_failure_does_not_open_visual(self):
        with patch.object(sys, "argv", self.argv), patch("run_pipeline.subprocess.run") as run, patch("run_pipeline.webbrowser.open") as browser:
            run.side_effect = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=2)]
            self.assertEqual(run_pipeline.main(), 2)
        browser.assert_not_called()
