import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from find_company_website import find_company_website
from find_company_website import search_web
from ddgs.exceptions import DDGSException
from assess_job import assess_job, select_company_candidate


class CompanyTests(unittest.TestCase):
    def test_legal_suffix_and_brand_at_end_of_title(self):
        result = find_company_website("Café Acme, Inc.", searcher=lambda q: [
            {"url": "https://different-brand.com", "title": "About Us | Cafe Acme"}])
        self.assertEqual(result["status"], "candidate_found")
        self.assertIn("company_name_matches_title_segment", result["candidates"][0]["name_match_reasons"])

    def test_substring_does_not_match(self):
        result = find_company_website("Acme", searcher=lambda q: [
            {"url": "https://acmescam.com", "title": "Acme Scam Reports"}])
        self.assertFalse(result["candidates"][0]["name_match"])
        self.assertEqual(result["status"], "no_matching_candidate")

    def test_empty_search_retries_with_context(self):
        searcher = Mock(side_effect=[[], [{"url": "https://acme.com", "title": "Acme"}]])
        result = find_company_website("Acme", location="SF", searcher=searcher)
        self.assertEqual(searcher.call_count, 2)
        self.assertEqual(result["status"], "candidate_found")
        self.assertTrue(result["candidates"][0]["name_match"])

    def test_contextual_title_can_upgrade_existing_domain(self):
        searcher = Mock(side_effect=[
            [{"url": "https://different-brand.com", "title": "Home"}],
            [{"url": "https://different-brand.com/about", "title": "About | Acme"}],
        ])
        result = find_company_website("Acme", location="SF", searcher=searcher)
        self.assertTrue(result["candidates"][0]["name_match"])
        self.assertEqual(len(result["candidates"][0]["evidence"]), 2)

    def test_nonmatching_candidates_excluded(self):
        client = Mock()
        lookup = {"candidates": [
            {"website": "https://acme.com/", "name_match": True},
            {"website": "https://hotel.com/", "name_match": False}],
            "searches": [{"results": [{"url": "https://hotel.com/"}]}]}
        client.chat.return_value = SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({
            "selected_url": "https://hotel.com/", "match_strength": "tentative",
            "reason": "Wrong result", "remaining_uncertainty": "Unknown"})))
        result = select_company_candidate({"company": "Acme"}, lookup, client)
        self.assertIsNone(result["selected_url"])
        arguments = client.chat.call_args.kwargs
        self.assertNotIn("hotel.com", arguments["messages"][1]["content"])
        self.assertEqual(arguments["format"]["properties"]["selected_url"]["enum"], ["https://acme.com/", None])
        self.assertEqual(len(lookup["candidates"]), 2)

    def test_no_name_matches_skip_model_selection(self):
        client = Mock()
        result = select_company_candidate({}, {"candidates": [{"website": "https://hotel.com/", "name_match": False}]}, client)
        client.chat.assert_not_called()
        self.assertIsNone(result["selected_url"])
        self.assertEqual(result["reason"], "No matching candidate")

    def test_contextual_search_preserves_both_queries_and_original_candidates(self):
        searcher = Mock(side_effect=[
            [{"url": "https://acme.com", "title": "Acme"}, {"url": "https://acme.org", "title": "Acme"}],
            [{"url": "https://acme.org/about", "title": "Acme", "description": "San Francisco robotics"}],
        ])
        result = find_company_website("Acme", location="San Francisco", job_title="Engineer", searcher=searcher)
        self.assertEqual(searcher.call_count, 2)
        self.assertEqual(len(result["searches"]), 2)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(len(result["candidates"][1]["evidence"]), 2)
        self.assertEqual(result["status"], "ambiguous")

    def test_contextual_failure_preserves_first_search(self):
        searcher = Mock(side_effect=[
            [{"url": "https://acme.com", "title": "Acme"}, {"url": "https://acme.org", "title": "Acme"}],
            DDGSException("Blocked"),
        ])
        result = find_company_website("Acme", location="SF", searcher=searcher)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIn("Blocked", result["errors"][0])

    def test_unique_match_skips_context_search(self):
        searcher = Mock(return_value=[{"url": "https://acme.com", "title": "Acme"}])
        find_company_website("Acme", location="SF", searcher=searcher)
        self.assertEqual(searcher.call_count, 1)

    def test_selection_rejects_invented_url_and_accepts_unresolved(self):
        client = Mock()
        lookup = {"candidates": [{"website": "https://acme.com/", "name_match": True}]}
        def respond(url, strength):
            client.chat.return_value = SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({
                "selected_url": url, "match_strength": strength, "reason": "Evidence", "remaining_uncertainty": "Unverified"})))
            return select_company_candidate({"company": "Acme"}, lookup, client)
        self.assertIsNone(respond("https://invented.com/", "tentative")["selected_url"])
        self.assertIsNone(respond(None, "unresolved")["selected_url"])
        self.assertEqual(respond("https://acme.com/", "tentative")["selected_url"], "https://acme.com/")

    def test_ambiguous_selection_reaches_investigator(self):
        posting = {"company": "Acme", "location": "SF", "application_url": "https://acme.org/apply"}
        finder = Mock(return_value={"status": "ambiguous", "candidates": [
            {"website": "https://acme.com/", "name_match": True}, {"website": "https://acme.org/", "name_match": True}], "company_website": None})
        selection = {"selected_url": "https://acme.org/", "match_strength": "tentative",
                     "reason": "Location evidence", "remaining_uncertainty": "Unverified"}
        call = SimpleNamespace(function=SimpleNamespace(name="inspect_application_url", arguments={
            "application_url": posting["application_url"], "company_website": "https://acme.org/"}))
        client = Mock()
        client.chat.side_effect = [
            SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps(selection))),
            SimpleNamespace(message=SimpleNamespace(tool_calls=[call])),
            SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": "insufficient evidence", "explanation": "Unverified"}))),
        ]
        investigator = Mock(return_value={"domain_matches_company": True})
        result = assess_job(posting, company_finder=finder, client=client, investigator=investigator, include_trace=True)
        investigator.assert_called_once_with(posting["application_url"], "https://acme.org/")
        self.assertEqual(result["company_lookup"]["selection"], selection)
        self.assertFalse(result["company_lookup"]["verified"])
        self.assertNotIn("company_website", posting)

    def test_ddgs_results_are_normalized(self):
        with patch("find_company_website.DDGS") as ddgs:
            ddgs.return_value.text.return_value = [{"href": "https://acme.com", "title": "Acme", "body": "Company site"}]
            self.assertEqual(search_web("Acme"), [{"url": "https://acme.com", "title": "Acme", "description": "Company site"}])
            ddgs.return_value.text.assert_called_once_with("Acme", max_results=5, backend="auto")

    def test_ddgs_failure_is_recorded(self):
        result = find_company_website("Acme", searcher=Mock(side_effect=DDGSException("Rate limited")))
        self.assertEqual(result["status"], "lookup_failed")
        self.assertEqual(result["provider"], "ddgs")
        self.assertEqual(result["errors"], ["Rate limited"])

    def test_directory_ignored_and_candidate_retains_source(self):
        rows = [{"url": "https://linkedin.com/company/acme", "title": "Acme"},
                {"url": "https://saasworthy.com/product/acme", "title": "Acme - Features"},
                {"url": "https://acme.co.uk/about", "title": "Acme | About us"}]
        result = find_company_website("Acme", searcher=lambda q: rows)
        self.assertEqual(result["company_website"], "https://acme.co.uk/")
        self.assertFalse(result["verified"])
        self.assertEqual(result["candidates"][0]["source_url"], "https://acme.co.uk/about")

    def test_multiple_matches_are_ambiguous(self):
        rows = [{"url": "https://acme.com", "title": "Acme"},
                {"url": "https://acme.org", "title": "Acme"}]
        result = find_company_website("Acme", searcher=lambda q: rows)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["company_website"])

    def test_unrelated_result_not_selected(self):
        result = find_company_website("Acme", searcher=lambda q: [{"url": "https://example.com", "title": "Other business"}])
        self.assertIsNone(result["company_website"])

    def test_duplicates_not_ambiguous(self):
        rows = [{"url": "https://acme.com/about", "title": "Acme"},
                {"url": "https://www.acme.com/contact", "title": "Acme"}]
        result = find_company_website("Acme", searcher=lambda q: rows)
        self.assertEqual(result["status"], "candidate_found")

    def test_missing_company_skips_search(self):
        searcher = Mock()
        self.assertEqual(find_company_website(None, searcher=searcher)["status"], "missing_company")
        searcher.assert_not_called()

    def test_provider_failure_is_evidence_limitation(self):
        result = find_company_website("Acme", searcher=Mock(side_effect=ValueError("Provider unavailable")))
        self.assertEqual(result["status"], "lookup_failed")
        self.assertIsNone(result["company_website"])

    def test_enrichment_reaches_url_tool_without_mutating_posting(self):
        posting = {"company": "Acme", "company_website": None, "application_url": "https://acme.com/apply"}
        lookup = {"company_website": "https://acme.com/", "verified": False}
        finder = Mock(return_value=lookup)
        investigator = Mock(return_value={"final_domain": "acme.com"})
        call = SimpleNamespace(function=SimpleNamespace(name="inspect_application_url", arguments={"application_url": posting["application_url"], "company_website": "https://acme.com/"}))
        client = Mock()
        client.chat.side_effect = [SimpleNamespace(message=SimpleNamespace(tool_calls=[call])),
                                  SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": "insufficient evidence", "explanation": "Unverified candidate"})))]
        result = assess_job(posting, client=client, investigator=investigator, company_finder=finder, include_trace=True)
        investigator.assert_called_once_with(posting["application_url"], "https://acme.com/")
        self.assertIsNone(posting["company_website"])
        self.assertEqual(result["company_lookup"], lookup)
        messages = client.chat.call_args.kwargs["messages"]
        self.assertIsNone(json.loads(messages[1]["content"])["company_website"])

    def test_supplied_website_skips_lookup(self):
        finder = Mock()
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": "low", "explanation": "Not verified"})))
        assess_job({"company": "Acme", "company_website": "https://acme.com"}, client=client, company_finder=finder)
        finder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
