import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assess_job import assess_job


def selection(*requests):
    return SimpleNamespace(message=SimpleNamespace(tool_calls=[
        SimpleNamespace(function=SimpleNamespace(name=name, arguments=args)) for name, args in requests]))


def final():
    return SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({
        "risk_level": "insufficient evidence", "explanation": "Registry history does not verify employer identity."})))


class CompanyDomainTests(unittest.TestCase):
    def test_selected_website_is_used_and_result_reaches_final_prompt(self):
        client = Mock()
        client.chat.side_effect = [selection(("get_company_domain_info", {"domain": "application.com"}),
            ("get_company_domain_info", {}), ("get_company_domain_info", {})), final()]
        registry = {"domain": "company.com", "lookup_success": True,
                    "domain_created": "2020-01-01T00:00:00Z", "domain_age_days": 2000}
        with patch("assess_job.get_domain_info", return_value=registry) as lookup:
            report = assess_job({"company_website": "https://company.com"}, client=client, include_trace=True)
        lookup.assert_called_once_with("https://company.com")
        trace = report["investigation"]["tool_calls"]
        self.assertIn("error", trace[0]["evidence"])
        self.assertIn("error", trace[2]["evidence"])
        self.assertEqual(trace[1]["evidence"]["scope"], "candidate_company_website_registration")
        self.assertEqual(report["claim_checks"]["company_website"]["status"], "unresolved")
        self.assertTrue(any(e["type"] == "company_domain_registration" for e in report["evidence_sources"]))
        self.assertIn('"company_domain_observations"', client.chat.call_args.kwargs["messages"][-1]["content"])
        self.assertIn('"domain_age_days": 2000', client.chat.call_args.kwargs["messages"][-1]["content"])

    def test_not_available_without_selected_website(self):
        client = Mock()
        client.chat.side_effect = [selection(("get_company_domain_info", {})), final()]
        investigator = Mock(return_value={})
        with patch("assess_job.get_domain_info") as lookup:
            report = assess_job({"application_url": "https://application.com"}, client=client,
                                investigator=investigator, include_trace=True)
        lookup.assert_not_called()
        self.assertIn("error", report["investigation"]["tool_calls"][0]["evidence"])

    def test_lookup_failure_remains_unresolved(self):
        client = Mock()
        client.chat.side_effect = [selection(("get_company_domain_info", {})), final()]
        with patch("assess_job.get_domain_info", return_value={"domain": "company.com", "lookup_success": False, "errors": [{"message": "timeout"}]}):
            report = assess_job({"company_website": "https://company.com"}, client=client, include_trace=True)
        self.assertEqual(report["claim_checks"]["company_website"]["status"], "unresolved")
        self.assertIn("lookup failure is not fraud", report["claim_checks"]["company_website"]["reason"])
