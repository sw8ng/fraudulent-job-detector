import sys
import unittest
import json
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from summary_grounding import observed_facts, summary_issues, factual_summary
from assess_job import assess_job


class SummaryTests(unittest.TestCase):
    def test_bad_summary_repaired_without_rerunning_tools_or_changing_risk(self):
        client = Mock()
        tool = SimpleNamespace(function=SimpleNamespace(name="inspect_application_url", arguments={"application_url": "https://example.com"}))
        def response(text, risk="low"):
            return SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": risk, "explanation": text})))
        client.chat.side_effect = [SimpleNamespace(message=SimpleNamespace(tool_calls=[tool])),
            response("The application redirects to example.com."), response("The URL stayed on example.com.", "high")]
        investigator = Mock(return_value={"redirect_lookup_success": True, "redirects": [], "final_domain": "example.com", "http_status": 200})
        result = assess_job({"application_url": "https://example.com"}, client=client, investigator=investigator, include_trace=True, model="test-model")
        investigator.assert_called_once()
        self.assertEqual(result["assessment"]["risk_level"], "low")
        self.assertEqual(result["investigation"]["summary_review"]["status"], "model_revised")
        self.assertNotIn("redirects to", result["assessment"]["summary"])

    def test_repeated_bad_wording_uses_recorded_facts(self):
        client = Mock()
        tool = SimpleNamespace(function=SimpleNamespace(name="inspect_application_url", arguments={"application_url": "https://example.com"}))
        bad = SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": "low", "explanation": "It redirects to example.com."})))
        client.chat.side_effect = [SimpleNamespace(message=SimpleNamespace(tool_calls=[tool])), bad, bad]
        investigator = Mock(return_value={"redirect_lookup_success": True, "redirects": [], "final_domain": "example.com", "http_status": 200})
        result = assess_job({"application_url": "https://example.com"}, client=client, investigator=investigator, include_trace=True, model="test-model")
        self.assertEqual(result["investigation"]["summary_review"]["status"], "factual_fallback")
        self.assertIn("0 HTTP redirects", result["assessment"]["summary"])
        investigator.assert_called_once()

    def facts(self, redirects=None):
        return observed_facts({"tool_calls": [{"tool": "inspect_application_url", "evidence": {
            "redirect_lookup_success": True, "redirects": redirects or [], "http_status": 200,
            "final_domain": "linkedin.com", "company_domain": "roblox.com",
            "domain_created": "2002-11-02T15:38:11Z"}}]})

    def test_unobserved_redirect_is_detected_but_negation_is_not(self):
        self.assertTrue(summary_issues("The application redirects to LinkedIn.", self.facts()))
        self.assertFalse(summary_issues("The application did not redirect to LinkedIn.", self.facts()))
        self.assertFalse(summary_issues("The application redirects to LinkedIn.", self.facts([{"status": 302}])))

    def test_application_registration_metadata_is_not_used(self):
        facts = self.facts()
        self.assertNotIn("registration_date", facts["url_observations"][0])
        self.assertNotIn("registration_domain", facts["url_observations"][0])
        self.assertNotIn("2002", factual_summary(facts))

    def test_candidate_is_not_official_and_fallback_has_no_detected_errors(self):
        self.assertTrue(summary_issues("The official website is roblox.com.", self.facts()))
        self.assertFalse(summary_issues("We did not establish an official website.", self.facts()))
        self.assertFalse(summary_issues(factual_summary(self.facts()), self.facts()))

    def test_application_404_must_not_be_attributed_to_successful_company_page(self):
        facts = observed_facts({"company_website_used": "https://example.com", "employer_association": {
            "status": "not_found_in_checked_pages", "pages_checked": [{"url": "https://example.com", "http_status": 200}],
            "errors": [{"url": "https://example.com/careers", "http_status": 404}]}})
        self.assertTrue(summary_issues("The application shares the domain with the supplied company website, but both return HTTP 404 errors.", facts))
        self.assertFalse(summary_issues("The company website was read successfully; the application returned 404.", facts))
        self.assertFalse(summary_issues("The company website returned 404 errors for standard careers paths.", facts))
        self.assertEqual(facts["association_page_errors"][0]["http_status"], 404)

    def test_unperformed_search_and_overcertainty_are_detected(self):
        self.assertTrue(summary_issues("No matching job was found.", self.facts()))
        self.assertTrue(summary_issues("These are definitive indicators of fraud.", self.facts()))
        self.assertTrue(summary_issues("These are definitive fraud indicators.", self.facts()))
        self.assertTrue(summary_issues("These are definitive scam indicators.", self.facts()))
        self.assertTrue(summary_issues("This is a legitimate domain registered in 2004.", self.facts()))

    def test_company_registration_is_separate_in_facts_and_fallback(self):
        facts = observed_facts({"tool_calls": [{"tool": "get_company_domain_info", "evidence": {
            "domain": "company.com", "lookup_success": True, "domain_created": "2020-01-01", "domain_age_days": 2000}}]})
        self.assertEqual(facts["company_domain_observations"][0]["domain"], "company.com")
        self.assertEqual(facts["url_observations"], [])
        self.assertIn("separate company-domain lookup for company.com", factual_summary(facts))

    def test_company_registration_must_not_be_attributed_to_application_domain(self):
        facts = self.facts()
        facts["company_domain_observations"] = [{"domain": "roblox.com", "lookup_success": True, "domain_created": "2004-01-30"}]
        self.assertTrue(summary_issues("linkedin.com was registered in 2004.", facts))
        self.assertFalse(summary_issues("roblox.com was registered in 2004.", facts))

