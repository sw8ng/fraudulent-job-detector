import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assessment_report import build_report
from quality_evaluation import evaluate_report, load_ground_truth, attach_quality
from compare_models import main
from saved_results import posting_fingerprint


class QualityEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.job = {"company": "Example", "application_url": "https://example.com/job"}
        self.fingerprint = posting_fingerprint(self.job)
        self.truth = {"schema_version": 1, "case_id": "closed-role", "input_fingerprint": self.fingerprint,
            "expected_tools": ["inspect_application_url", "collect_application_page"],
            "useful_tools": ["inspect_application_url", "collect_application_page", "get_company_domain_info"],
            "important_findings": [{"id": "posting_closed", "evidence_type": "application_page_content",
                                    "source_quote": "this posting is no longer available"}],
            "unsupported_claims": [{"id": "invented_redirect", "rule": "redirect_claim_without_observed_redirect"}]}
        investigation = {"model": "any-future-model", "input_fingerprint": self.fingerprint,
            "tool_calls": [
                {"tool": "inspect_application_url", "round": 1, "seconds": 1, "evidence": {
                    "redirect_lookup_success": True, "http_status": 200, "redirects": [],
                    "domain_matches_company": True, "final_domain": "example.com"}},
                {"tool": "collect_application_page", "round": 2, "seconds": 1, "evidence": {
                    "extraction_success": True, "text": "Sorry, this posting is no longer available."}}]}
        self.report = build_report(self.job, {"risk_level": "insufficient evidence", "explanation": "The role has closed.",
            "findings": [{"id": "posting_closed", "quote": "this posting is no longer available",
                          "evidence_ids": ["application-page-2"]}]}, investigation)

    def test_supported_paraphrase_uses_finding_id_and_source_not_summary_string(self):
        quality = evaluate_report(self.report, self.truth)
        self.assertEqual(quality["finding_recall"]["value"], 1)
        self.assertEqual(quality["tool_recall"]["value"], 1)
        self.assertEqual(quality["tool_precision"]["value"], 1)

    def test_model_can_quote_a_different_valid_source_excerpt(self):
        self.report['assessment']['findings'][0]['quote'] = 'posting is no longer available'
        self.assertEqual(evaluate_report(self.report, self.truth)['finding_recall']['value'], 1)

    def test_tags_cannot_get_credit_without_supporting_evidence(self):
        for field, value in (("quote", "Applications are open"), ("evidence_ids", ["invented-id"])):
            report = copy.deepcopy(self.report)
            report["assessment"]["findings"][0][field] = value
            quality = evaluate_report(report, self.truth)
            self.assertEqual(quality["finding_recall"]["value"], 0)
            self.assertEqual(quality["unsupported_claims"]["count"], 1)

    def test_pipeline_findings_do_not_backfill_legacy_model_findings(self):
        del self.report["assessment"]["findings"]
        quality = evaluate_report(self.report, self.truth)
        self.assertIsNone(quality["finding_recall"])
        self.assertEqual(quality["finding_status"], "legacy_missing_finding_ids")

    def test_missing_finding_is_zero_not_legacy(self):
        self.report["assessment"]["findings"] = []
        self.assertEqual(evaluate_report(self.report, self.truth)["finding_recall"]["value"], 0)

    def test_duplicate_rejected_and_unnecessary_calls_reduce_precision(self):
        calls = self.report["investigation"]["tool_calls"]
        calls.extend([{"tool": "inspect_application_url", "evidence": {"error": "Already executed"}},
                      {"tool": "unnecessary", "evidence": {"error": "Unknown tool"}}])
        quality = evaluate_report(self.report, self.truth)
        self.assertEqual(quality["tool_recall"]["value"], 1)
        self.assertEqual(quality["tool_precision"]["value"], 0.5)

    def test_blocked_page_does_not_make_selection_incorrect(self):
        self.report["investigation"]["tool_calls"][1]["evidence"] = {"http_status": 403, "extraction_success": False}
        self.assertEqual(evaluate_report(self.report, self.truth)["tool_recall"]["value"], 1)

    def test_available_conditions_and_zero_denominators(self):
        self.truth["expected_tools"] = [{"name": "collect_application_page", "when": {
            "evidence_type": "url_inspection", "field": "http_status", "equals": 403}}]
        quality = evaluate_report(self.report, self.truth)
        self.assertIsNone(quality["tool_recall"]["value"])
        self.truth["expected_tools"] = []
        self.truth["important_findings"] = []
        self.report["investigation"]["tool_calls"] = []
        quality = evaluate_report(self.report, self.truth)
        self.assertIsNone(quality["tool_precision"]["value"])
        self.assertIsNone(quality["finding_recall"]["value"])

    def test_known_redirect_violation_and_negation(self):
        self.report["assessment"]["summary"] = "The application redirected to example.com."
        self.assertEqual(evaluate_report(self.report, self.truth)["unsupported_claims"]["count"], 1)
        self.report["assessment"]["summary"] = "The application did not redirect to another page."
        self.assertEqual(evaluate_report(self.report, self.truth)["unsupported_claims"]["count"], 0)

    def test_negated_wording_is_not_validation_failure(self):
        self.report["assessment"]["summary"] = "This is not definitive fraud evidence. The absence of a verified domain is a limitation."
        self.assertEqual(evaluate_report(self.report, self.truth)["validation_flags"], [])

    def test_structured_expectations_and_forbidden_finding(self):
        self.truth["structured_expectations"] = [{"path": "claim_checks.application_domain.status", "equals": "supported"}]
        self.truth["unsupported_claims"] = [{"id": "closed_not_allowed", "rule": "forbidden_finding", "finding_id": "posting_closed"}]
        quality = evaluate_report(self.report, self.truth)
        self.assertTrue(quality["structured_expectations"][0]["passed"])
        self.assertEqual(quality["unsupported_claims"]["count"], 1)

    def test_fingerprint_and_completion_are_not_silently_graded(self):
        report = copy.deepcopy(self.report)
        report["investigation"]["input_fingerprint"] = "other"
        self.assertEqual(evaluate_report(report, self.truth)["status"], "input_mismatch")
        self.report["execution_status"] = "timed_out"
        self.assertEqual(evaluate_report(self.report, self.truth)["status"], "incomplete_assessment")

    def test_truth_validation_rejects_duplicates_and_unknown_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"case.json"
            for changes in ({"expected_tools": ["inspect_application_url", "inspect_application_url"]},
                            {"unsupported_claims": [{"id": "bad", "rule": "arbitrary_code"}]}):
                path.write_text(json.dumps(dict(self.truth, **changes)), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_ground_truth(directory)

    def test_offline_cli_preserves_files_and_measurements_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/"source"
            source.mkdir()
            truth_dir = root/"truth"
            truth_dir.mkdir()
            (truth_dir/"case.json").write_text(json.dumps(self.truth), encoding="utf-8")
            report_path = source/"report.json"
            report_path.write_text(json.dumps(self.report), encoding="utf-8")
            experiment = {"models": ["any-future-model"], "cases": [{"name": "job.json", "source": "source-job", "job": self.job}],
                "runs": [{"model": "any-future-model", "case": "job.json", "source": "source-job", "status": "completed",
                          "report": "report.json", "seconds": 42, "model_seconds": 40, "model_calls": []}]}
            comparison = source/"comparison.json"
            comparison.write_text(json.dumps(experiment), encoding="utf-8")
            before = comparison.read_bytes(), report_path.read_bytes()
            output = root/"evaluated"
            argv = ["compare_models.py", "--evaluate-saved", str(comparison), "--ground-truth-dir", str(truth_dir), "--output", str(output)]
            with patch.object(sys, "argv", argv), patch("compare_models.Client", side_effect=AssertionError("No network")), \
                 patch("compare_models.run_bounded_assessment", side_effect=AssertionError("No model")):
                main()
            evaluated = json.loads((output/"comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(evaluated["runs"][0]["seconds"], 42)
            self.assertEqual(evaluated["runs"][0]["model_calls"], [])
            self.assertEqual(evaluated["runs"][0]["quality"]["status"], "evaluated")
            self.assertEqual(before, (comparison.read_bytes(), report_path.read_bytes()))
            self.assertEqual(before[1], (output/"report.json").read_bytes())
            html = (output/"comparison.html").read_text(encoding="utf-8")
            self.assertIn("Workflow and Evidence Checks", html)
            self.assertIn("Time per Run: Individual Calls", html)
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                main()

    def test_closed_notice_contradiction_and_negated_claim(self):
        self.truth['unsupported_claims'] = [{'id': 'closed_contradiction', 'rule': 'open_claim_with_closed_evidence'}]
        self.report['assessment']['summary'] = 'The job listing is not expired, but the page says no longer available.'
        self.assertEqual(evaluate_report(self.report, self.truth)['unsupported_claims']['count'], 1)
        self.report['assessment']['summary'] = 'It is not true that the job listing is open.'
        self.assertEqual(evaluate_report(self.report, self.truth)['unsupported_claims']['count'], 0)

    def test_unavailable_legacy_rules_are_not_reported_as_zero_violations(self):
        del self.report['assessment']['findings']
        self.truth['unsupported_claims'] = [{'id': 'forbidden', 'rule': 'forbidden_finding', 'finding_id': 'posting_closed'}]
        metric = evaluate_report(self.report, self.truth)['unsupported_claims']
        self.assertIsNone(metric['count'])
        self.assertEqual(metric['checks_unavailable'], ['forbidden'])

    def test_candidate_website_exists_condition(self):
        self.truth['expected_tools'] = [{'name': 'check_employer_association', 'when': {'path': 'candidate_company_website', 'exists': True}}]
        self.assertIsNone(evaluate_report(self.report, self.truth)['tool_recall']['value'])
        self.report['candidate_company_website'] = 'https://example.com'
        self.assertEqual(evaluate_report(self.report, self.truth)['tool_recall']['value'], 0)

    def test_careers_match_requires_actual_match_evidence(self):
        self.truth['important_findings'] = [{'id': 'job_match_found', 'evidence_type': 'employer_association'}]
        self.report['evidence_sources'].append({'id': 'association-1', 'type': 'employer_association', 'evidence': {'status': 'matching_posting_found', 'matches': [{'url': 'https://example.com/job'}]}})
        self.report['assessment']['findings'] = [{'id': 'job_match_found', 'quote': '', 'evidence_ids': ['association-1']}]
        self.assertEqual(evaluate_report(self.report, self.truth)['finding_recall']['value'], 1)
        self.report['evidence_sources'][-1]['evidence']['matches'] = []
        self.assertEqual(evaluate_report(self.report, self.truth)['finding_recall']['value'], 0)

    def test_missing_ground_truth_is_na(self):
        experiment = {"cases": [{"name": "job.json", "source": "source-job", "job": self.job}],
                      "runs": [{"model": "any-model", "source": "source-job"}]}
        row = attach_quality(experiment, Path('.'), {})["runs"][0]
        self.assertEqual(row["quality"]["status"], "missing_ground_truth")
        self.assertNotIn("quality", experiment["runs"][0])
