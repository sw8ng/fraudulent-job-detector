import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assessment_report import build_report, validate_evidence_references


class ReportTests(unittest.TestCase):
    assessment = {"risk_level": "insufficient evidence", "explanation": "Evidence is limited."}

    def report(self, **investigation):
        return build_report({}, self.assessment, investigation)

    def test_skipped_claims_remain_not_checked(self):
        report = self.report(employer_association={"status": "not_requested"})
        self.assertEqual(report["claim_checks"]["job_on_company_careers_page"]["status"], "not_checked")
        self.assertEqual(report["claim_checks"]["recruiter_affiliation"]["status"], "not_checked")
        self.assertEqual(report["assessment"]["summary"], "Evidence is limited.")
        self.assertEqual(report["unresolved"], [])
        self.assertEqual(len(report["not_checked"]), 5)

    def test_failed_association_is_unresolved_not_conflicting(self):
        report = self.report(employer_association={"status": "could_not_check", "errors": [{"message": "Timeout"}]})
        check = report["claim_checks"]["job_on_company_careers_page"]
        self.assertEqual(check["status"], "unresolved")
        self.assertEqual(check["evidence_ids"], ["association-1"])
        self.assertEqual([item["claim"] for item in report["unresolved"]], ["job_on_company_careers_page"])
        self.assertTrue(all(item["status"] == "not_checked" for item in report["not_checked"]))

    def test_rejected_request_is_not_checked(self):
        report = self.report(tool_calls=[{"tool": "inspect_application_url", "evidence": {"error": "Invalid URL argument"}}])
        self.assertEqual(report["claim_checks"]["application_domain"]["status"], "not_checked")

    def test_matching_job_does_not_verify_identity(self):
        report = self.report(company_website_used="https://example.com", employer_association={
            "status": "matching_posting_found", "matches": [{"url": "https://ats.example.org/jobs/1"}]})
        self.assertEqual(report["claim_checks"]["job_on_company_careers_page"]["status"], "supported")
        self.assertEqual(report["claim_checks"]["company_identity"]["status"], "not_checked")
        self.assertEqual(report["claim_checks"]["company_website"]["status"], "unresolved")

    def test_different_domain_does_not_prove_conflict_and_date_is_scoped(self):
        report = self.report(tool_calls=[{"tool": "inspect_application_url", "evidence": {
            "redirect_lookup_success": True, "domain_matches_company": False,
            "final_domain": "linkedin.com", "domain_created": "2002-11-02"}}])
        self.assertEqual(report["claim_checks"]["application_domain"]["status"], "unresolved")
        self.assertEqual(report["evidence_sources"][-1]["evidence"]["final_domain"], "linkedin.com")
        self.assertEqual(report["key_findings"]["suspicious"], [])

    def test_content_concerns_have_evidence_references_and_trace_is_preserved(self):
        investigation = {"content_analysis": {"observations": [{"assessment": "concern", "quote": "Pay a fee", "interpretation": "Applicant payment demand"}]}}
        report = self.report(**investigation)
        self.assertEqual(report["investigation"], investigation)
        self.assertEqual(report["key_findings"]["suspicious"][0]["evidence_ids"], ["content-1"])
        broken = copy.deepcopy(report)
        broken["key_findings"]["suspicious"][0]["evidence_ids"] = ["invented-1"]
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_evidence_references(broken)
