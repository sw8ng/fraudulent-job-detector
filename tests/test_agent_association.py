import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assess_job import assess_job


def calls(*requests):
    return SimpleNamespace(message=SimpleNamespace(tool_calls=[
        SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))
        for name, arguments in requests]))


def final():
    return SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({
        "risk_level": "insufficient evidence", "explanation": "Candidate association only"})))


class AgentAssociationTests(unittest.TestCase):
    job = {"company_website": "https://example.com/", "job_title": "Engineer", "location": "SF"}
    arguments = {"company_website": "https://example.com/", "job_title": "Engineer", "location": "SF"}

    def test_requested_association_reaches_final_and_is_recorded(self):
        client = Mock()
        client.chat.side_effect = [calls(("check_employer_association", self.arguments)), final()]
        evidence = {"status": "matching_posting_found", "verified": False}
        with patch("assess_job.check_employer_association", return_value=evidence) as checker:
            result = assess_job(self.job, client=client, include_trace=True)
        checker.assert_called_once_with(**self.arguments)
        self.assertEqual(result["investigation"]["employer_association"], evidence)
        self.assertEqual(result["investigation"]["tool_calls"][0]["tool"], "check_employer_association")
        self.assertTrue(any(isinstance(m, dict) and m.get("role") == "tool" for m in client.chat.call_args.kwargs["messages"]))

    def test_agent_may_skip_association(self):
        client = Mock()
        client.chat.side_effect = [calls(), final()]
        with patch("assess_job.check_employer_association") as checker:
            result = assess_job(self.job, client=client, include_trace=True)
        checker.assert_not_called()
        self.assertEqual(result["investigation"]["employer_association"]["status"], "not_requested")

    def test_unselected_site_rejected(self):
        client = Mock()
        client.chat.side_effect = [calls(("check_employer_association", {**self.arguments, "company_website": "https://other.com"})), final()]
        with patch("assess_job.check_employer_association") as checker:
            result = assess_job(self.job, client=client, include_trace=True)
        checker.assert_not_called()
        self.assertIn("error", result["investigation"]["tool_calls"][0]["evidence"])

    def test_two_rounds_and_duplicate_prevention(self):
        job = {**self.job, "application_url": "https://example.com/apply"}
        client = Mock()
        client.chat.side_effect = [calls(("inspect_application_url", {"application_url": job["application_url"]})),
            calls(("inspect_application_url", {"application_url": job["application_url"]}),
                  ("check_employer_association", self.arguments)), final()]
        investigator = Mock(return_value={"final_domain": "example.com"})
        with patch("assess_job.check_employer_association", return_value={"status": "could_not_check"}) as checker:
            result = assess_job(job, client=client, investigator=investigator, include_trace=True)
        investigator.assert_called_once()
        checker.assert_called_once()
        self.assertIn("Duplicate", result["investigation"]["tool_calls"][1]["evidence"]["error"])

