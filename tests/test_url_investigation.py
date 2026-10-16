import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inspect_application_url as url_tool
from assess_job import assess_job
from follow_redirects import follow_redirects
from get_domain_info import get_domain_info


class URLTests(unittest.TestCase):
    def test_standalone_trace_does_not_request_rdap(self):
        requested = []
        def handler(request):
            requested.append(str(request.url))
            if request.url.path == "/start":
                return httpx.Response(302, headers={"Location": "/end"})
            return httpx.Response(200)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch.object(url_tool, "validate_public_url"):
            result = follow_redirects("https://example.com/start", client=client)
        self.assertEqual(result["final_url"], "https://example.com/end")
        self.assertEqual(len(requested), 2)
        self.assertTrue(result["limitations"])

    def test_standalone_domain_lookup_normalizes_subdomain(self):
        requested = []
        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(200, json={"events": []})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.object(url_tool.httpx, "Client", return_value=client), patch.object(url_tool, "validate_public_url"):
            result = get_domain_info("https://jobs.example.co.uk/apply")
        self.assertEqual(result["domain"], "example.co.uk")
        self.assertTrue(result["lookup_success"])
        self.assertEqual(requested, ["https://rdap.org/domain/example.co.uk"])

    def investigate(self, handler, url="https://jobs.example.co.uk/apply", company="https://example.co.uk"):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.object(url_tool.httpx, "Client", return_value=client), patch.object(url_tool, "validate_public_url"):
            return url_tool.inspect_application_url(url, company)

    def test_redirect_and_registration(self):
        def handler(request):
            if request.url.host == "rdap.org":
                return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"}], "status": ["active"]})
            if request.url.path == "/apply":
                return httpx.Response(302, headers={"Location": "/form"})
            return httpx.Response(200)
        result = self.investigate(handler)
        self.assertEqual(result["final_url"], "https://jobs.example.co.uk/form")
        self.assertTrue(result["domain_matches_company"])
        self.assertTrue(result["lookup_success"])
        self.assertGreater(result["domain_age_days"], 2000)
        self.assertEqual(len(result["redirects"]), 1)

    def test_missing_date_and_company(self):
        result = self.investigate(lambda r: httpx.Response(200, json={}), company=None)
        self.assertIsNone(result["domain_age_days"])
        self.assertIsNone(result["domain_matches_company"])
        self.assertTrue(result["lookup_success"])

    def test_blocked_page_and_failed_rdap(self):
        result = self.investigate(lambda r: httpx.Response(403))
        self.assertEqual(result["http_status"], 403)
        self.assertFalse(result["lookup_success"])
        self.assertEqual({e["stage"] for e in result["errors"]}, {"http", "rdap"})

    def test_redirect_loop(self):
        result = self.investigate(lambda r: httpx.Response(302, headers={"Location": str(r.url)}))
        self.assertIsNone(result["final_url"])
        self.assertIn("loop", result["errors"][0]["message"])

    def test_private_redirect_blocked(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})))
        addresses = [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 80))]
        with patch.object(url_tool.httpx, "Client", return_value=client), patch.object(url_tool.socket, "getaddrinfo", side_effect=[[addresses[0]], [addresses[1]]]):
            result = url_tool.inspect_application_url("https://example.com")
        self.assertFalse(result["redirect_lookup_success"])
        self.assertIn("public IP", result["errors"][0]["message"])

    def test_invalid_scheme(self):
        result = url_tool.inspect_application_url("file:///etc/passwd")
        self.assertFalse(result["redirect_lookup_success"])


class AgentTests(unittest.TestCase):
    def test_final_assessment_retries_truncation(self):
        from unittest.mock import Mock
        client = Mock()
        client.chat.side_effect = [
            SimpleNamespace(done_reason="length", message=SimpleNamespace(content='{')),
            SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": "insufficient evidence", "explanation": "Not verified"}))),
        ]
        result = assess_job({}, client=client)
        self.assertEqual(result["risk_level"], "insufficient evidence")
        self.assertEqual(client.chat.call_count, 2)

    def test_repeated_truncation_stops_after_one_retry(self):
        from unittest.mock import Mock
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason="length", message=SimpleNamespace(content='{'))
        with self.assertRaisesRegex(ValueError, "retry"):
            assess_job({}, client=client)
        self.assertEqual(client.chat.call_count, 2)

    def run_agent(self, arguments):
        from unittest.mock import Mock
        call = SimpleNamespace(function=SimpleNamespace(name="inspect_application_url", arguments=arguments))
        client = Mock()
        client.chat.side_effect = [
            SimpleNamespace(message=SimpleNamespace(tool_calls=[call])),
            SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": "insufficient evidence", "explanation": "Not verified"}))),
        ]
        investigator = Mock(return_value={"lookup_success": False})
        result = assess_job({"application_url": "https://example.com", "company_website": None}, client=client, investigator=investigator, include_trace=True)
        return result, client, investigator

    def test_evidence_sent_back_to_model(self):
        result, client, investigator = self.run_agent({"application_url": "https://example.com"})
        investigator.assert_called_once_with("https://example.com", None)
        messages = client.chat.call_args.kwargs["messages"]
        self.assertTrue(any(isinstance(m, dict) and m.get("role") == "tool" for m in messages))
        self.assertFalse(result["investigation"]["tool_calls"][0]["evidence"]["lookup_success"])

    def test_invented_url_rejected(self):
        result, _, investigator = self.run_agent({"application_url": "https://other.com"})
        investigator.assert_not_called()
        self.assertIn("error", result["investigation"]["tool_calls"][0]["evidence"])

    def test_empty_missing_company_allowed(self):
        _, _, investigator = self.run_agent({"application_url": "https://example.com", "company_website": ""})
        investigator.assert_called_once_with("https://example.com", None)

    def test_unselected_company_ignored_without_blocking_inspection(self):
        result, client, investigator = self.run_agent({
            "application_url": "https://example.com", "company_website": "https://unselected.com/"})
        investigator.assert_called_once_with("https://example.com", None)
        entry = result["investigation"]["tool_calls"][0]
        self.assertEqual(entry["arguments"]["company_website"], "https://unselected.com/")
        self.assertIsNone(entry["effective_arguments"]["company_website"])


if __name__ == "__main__":
    unittest.main()
