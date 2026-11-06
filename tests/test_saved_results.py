import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from assessment_report import build_report
from saved_results import matching_report, posting_fingerprint
from compare_models import evaluation_cases, find_saved_run, main


class SavedResultsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.input_path = self.folder / "posting.json"
        self.job = {"description": "An ordinary posting."}
        self.input_path.write_text(json.dumps(self.job), encoding="utf-8")
        self.report_path = self.folder / "report-001.json"
        report = build_report(self.job, {"risk_level": "insufficient evidence", "explanation": "Limited evidence."}, {
            "model": "test-model", "input_fingerprint": posting_fingerprint(self.job)})
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        self.case = evaluation_cases([self.input_path])[0]
        self.previous = {
            "cases": [self.case], "models": ["test-model"], "repeats": 1,
            "runs": [{"case": self.case["name"], "model": "test-model", "repetition": 1,
                      "status": "completed", "report": self.report_path.name, "seconds": 12, "model_seconds": 11}],
        }
        (self.folder / "comparison.json").write_text(json.dumps(self.previous), encoding="utf-8")

    def test_only_supplied_input_is_used(self):
        cases = evaluation_cases([self.input_path])
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["job"], self.job)
        self.assertNotIn("SYNTHETIC", cases[0]["name"])

    def test_model_and_input_must_match(self):
        self.assertIsNotNone(matching_report(self.report_path, self.job, "test-model"))
        self.assertIsNone(matching_report(self.report_path, self.job, "different-model"))
        self.assertIsNone(matching_report(self.report_path, {"description": "Changed"}, "test-model"))

    def test_missing_or_corrupt_report_cannot_be_reused(self):
        self.report_path.write_text("broken JSON", encoding="utf-8")
        self.assertIsNone(find_saved_run(self.previous, self.case, "test-model", 1, self.folder))
        self.report_path.unlink()
        self.assertIsNone(find_saved_run(self.previous, self.case, "test-model", 1, self.folder))

    def test_saved_run_keeps_original_measurement(self):
        row = find_saved_run(self.previous, self.case, "test-model", 1, self.folder)
        self.assertTrue(row["reused"])
        self.assertEqual(row["seconds"], 12)

    def test_legacy_comparison_uses_saved_input_snapshot(self):
        report = json.loads(self.report_path.read_text())
        del report["investigation"]["input_fingerprint"]
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        self.assertIsNotNone(find_saved_run(self.previous, self.case, "test-model", 1, self.folder))
        changed = dict(self.case)
        changed["job"] = {"description": "Changed"}
        self.assertIsNone(find_saved_run(self.previous, changed, "test-model", 1, self.folder))

    def test_failed_run_is_not_reused(self):
        self.previous["runs"][0]["status"] = "failed"
        self.assertIsNone(find_saved_run(self.previous, self.case, "test-model", 1, self.folder))

    def test_ambiguous_legacy_filename_is_not_reused(self):
        other = dict(self.case)
        other["source"] = str(self.folder / "other" / self.input_path.name)
        self.previous["cases"].append(other)
        self.assertIsNone(find_saved_run(self.previous, self.case, "test-model", 1, self.folder))

    def test_comparison_skips_before_contacting_ollama(self):
        argv = ["compare_models.py", "--models", "test-model", "--inputs", str(self.input_path), "--output", str(self.folder)]
        with patch.object(sys, "argv", argv), patch("compare_models.Client") as client, patch("compare_models.run_case") as runner:
            main()
        client.assert_not_called()
        runner.assert_not_called()
        saved = json.loads((self.folder / "comparison.json").read_text())
        self.assertTrue(saved["runs"][0]["reused"])
        self.assertEqual(saved["runs"][0]["seconds"], 12)

    def test_folder_option_finds_files_without_powershell_variables(self):
        outputs = self.folder / "outputs"
        outputs.mkdir()
        posting = outputs / self.input_path.name
        posting.write_text(json.dumps(self.job), encoding="utf-8")
        self.previous["cases"] = evaluation_cases([posting])
        (self.folder / "comparison.json").write_text(json.dumps(self.previous), encoding="utf-8")
        argv = ["compare_models.py", "--models", "test-model", "--input-dir", str(outputs), "--output", str(self.folder)]
        with patch.object(sys, "argv", argv), patch("compare_models.Client") as client, patch("compare_models.run_case") as runner:
            main()
        client.assert_not_called()
        runner.assert_not_called()

    def test_empty_folder_fails_before_any_model_connection(self):
        outputs = self.folder / "empty"
        outputs.mkdir()
        argv = ["compare_models.py", "--models", "test-model", "--input-dir", str(outputs), "--output", str(self.folder)]
        with patch.object(sys, "argv", argv), patch("compare_models.Client") as client:
            with self.assertRaises(SystemExit):
                main()
        client.assert_not_called()

    def test_force_requests_fresh_work_and_preserves_old_report(self):
        argv = ["compare_models.py", "--models", "test-model", "--inputs", str(self.input_path), "--output", str(self.folder), "--force"]
        old_report = self.report_path.read_text()
        fresh = dict(self.previous["runs"][0])
        fresh["reused"] = False
        with patch.object(sys, "argv", argv), patch("compare_models.Client") as client, patch("compare_models.run_case", return_value=fresh) as runner:
            client.return_value.list.return_value = SimpleNamespace(models=[SimpleNamespace(model="test-model")])
            main()
        runner.assert_called_once()
        self.assertEqual(self.report_path.read_text(), old_report)

    def test_standalone_skip_works_with_ollama_unavailable(self):
        environment = dict(os.environ)
        environment["OLLAMA_HOST"] = "http://127.0.0.1:1"
        command = [sys.executable, str(ROOT / "scripts/assess_job.py"), str(self.input_path),
                   "--model", "test-model", "--output", str(self.report_path)]
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping", result.stdout)

    def test_standalone_changed_input_stops_before_model_work(self):
        self.input_path.write_text(json.dumps({"description": "Changed"}), encoding="utf-8")
        environment = dict(os.environ)
        environment["OLLAMA_HOST"] = "http://127.0.0.1:1"
        command = [sys.executable, str(ROOT / "scripts/assess_job.py"), str(self.input_path),
                   "--model", "test-model", "--output", str(self.report_path)]
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--force", result.stderr)
