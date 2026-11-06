import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_recruiter_email import check_recruiter_email, posting_emails
from assess_job import assess_job
from assessment_report import build_report


class EmailTests(unittest.TestCase):
    def check(self, address, website="https://company.com"):
        return check_recruiter_email({"contact_email": address}, website)["emails"][0]

    def test_company_subdomain_match(self):
        self.assertEqual(self.check("jobs@recruiting.company.com")["comparison"], "matching_domain")

    def test_deceptive_suffix_is_not_a_match(self):
        self.assertEqual(self.check("jobs@company.com.attacker.com")["comparison"], "different_domain")

    def test_free_provider_and_provider_lookalike(self):
        self.assertEqual(self.check("person@gmail.com")["comparison"], "free_provider")
        self.assertEqual(self.check("person@gmail.com.attacker.com")["comparison"], "different_domain")

    def test_no_website_does_not_invent_comparison(self):
        self.assertEqual(self.check("jobs@agency.com", None)["comparison"], "no_company_domain")

    def test_report_discloses_missing_company_comparison(self):
        result = check_recruiter_email({"contact_email": "jobs@agency.com"})
        report = build_report({}, {"risk_level": "insufficient evidence", "explanation": "No company website."}, {
            "tool_calls": [{"tool": "check_recruiter_email", "evidence": result}]})
        claim = report["claim_checks"]["recruiter_affiliation"]
        self.assertEqual(claim["status"], "unresolved")
        self.assertIn("no valid candidate company domain", claim["reason"])
        self.assertNotIn("were compared", claim["reason"])

    def test_case_and_idna(self):
        self.assertEqual(self.check("Jobs@COMPANY.COM")["comparison"], "matching_domain")
        self.assertEqual(self.check("jobs@bücher.de", "https://xn--bcher-kva.de")["comparison"], "matching_domain")

    def test_invalid_domain_and_website_are_disclosed(self):
        self.assertEqual(self.check("jobs@company.invalid")["comparison"], "invalid_domain")
        result = check_recruiter_email({"contact_email": "jobs@company.com"}, "mailto:jobs@company.com")
        self.assertIsNone(result["company_domain"])
        self.assertIn("could not be compared", result["limitations"][-1])

    def test_source_grounding_and_multiple_contacts(self):
        text = "Apply to jobs@company.com. Accessibility: access@gmail.com."
        records = posting_emails({"contact_email": "invented@company.com", "description": text, "raw_posting_text": text})
        self.assertEqual([r["email"] for r in records], ["jobs@company.com", "access@gmail.com"])
        self.assertTrue(all(r["present_in_raw_posting"] for r in records))
        self.assertIn("Accessibility", records[1]["quote"])

    def test_dedup_and_no_email(self):
        self.assertEqual(len(posting_emails({"contact_email": "Jobs@company.com", "description": "jobs@company.com"})), 1)
        self.assertEqual(check_recruiter_email({})["status"], "no_email_available")

    def test_raw_text_fallback_and_original_context(self):
        text = "For accessibility only, contact access@example.com."
        record = posting_emails({"contact_email": "access@example.com", "raw_posting_text": text})[0]
        self.assertIn("accessibility only", record["quote"])
        self.assertEqual(record["quote_source"], "raw_posting_text")
        self.assertEqual(len(posting_emails({"raw_posting_text": text})), 1)

    def test_match_does_not_upgrade_affiliation_or_create_suspicious_finding(self):
        for address in ("jobs@company.com", "jobs@gmail.com", "jobs@agency.com"):
            evidence = check_recruiter_email({"contact_email": address}, "https://company.com")
            report = build_report({}, {"risk_level": "low", "explanation": "Limited evidence."}, {
                "tool_calls": [{"tool": "check_recruiter_email", "evidence": evidence}]})
            self.assertEqual(report["claim_checks"]["recruiter_affiliation"]["status"], "unresolved")
            self.assertEqual(report["key_findings"]["suspicious"], [])
            self.assertEqual(report["claim_checks"]["recruiter_affiliation"]["evidence_ids"], ["email-1"])

    def test_agent_validates_arguments_and_executes_once(self):
        def call(args):
            return SimpleNamespace(function=SimpleNamespace(name="check_recruiter_email", arguments=args))
        client = Mock()
        client.chat.side_effect = [SimpleNamespace(message=SimpleNamespace(tool_calls=[
            call({"email": "invented@attacker.com"}), call({}), call({})])),
            SimpleNamespace(message=SimpleNamespace(tool_calls=[])),
            SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({
                "risk_level": "insufficient evidence", "explanation": "Email domain agreement does not establish affiliation."})))]
        report = assess_job({"contact_email": "jobs@company.com", "company_website": "https://company.com"}, client=client, include_trace=True, model="test-model")
        trace = report["investigation"]["tool_calls"]
        self.assertIn("error", trace[0]["evidence"])
        self.assertEqual(trace[1]["evidence"]["emails"][0]["comparison"], "matching_domain")
        self.assertIn("error", trace[2]["evidence"])
        self.assertEqual(report["claim_checks"]["recruiter_affiliation"]["status"], "unresolved")

    def test_unavailable_without_email(self):
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({
            "risk_level": "insufficient evidence", "explanation": "No contact information."})))
        report = assess_job({}, client=client, include_trace=True, model="test-model")
        self.assertEqual(report["claim_checks"]["recruiter_affiliation"]["status"], "not_checked")
        self.assertNotIn("tools", client.chat.call_args.kwargs)
