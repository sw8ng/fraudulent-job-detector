import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_content import analyze_content
from assess_job import assess_job


class ContentTests(unittest.TestCase):
    def client(self, data):
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps(data)))
        return client

    def test_exact_context_retained_and_fabricated_quote_dropped(self):
        client = self.client({"observations": [
            {"category": "payment", "quote": "We never charge application fees.", "interpretation": "Denies fees", "assessment": "context"},
            {"category": "payment", "quote": "Pay us $500", "interpretation": "Payment demand", "assessment": "concern"}],
            "claims_to_verify": [], "limitations": []})
        result = analyze_content({"raw_posting_text": "We never charge application fees."}, client=client)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["assessment"], "context")
        self.assertEqual(result["status"], "partial")

    def test_missing_text_skips_qwen(self):
        client = Mock()
        self.assertEqual(analyze_content({}, client=client)["status"], "no_text")
        client.chat.assert_not_called()

    def test_content_precedes_company_search_and_reaches_final_assessment(self):
        client = self.client({"observations": [], "claims_to_verify": [], "limitations": []})
        client.chat.side_effect = [client.chat.return_value,
            SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps({"risk_level": "insufficient evidence", "explanation": "Unverified"})))]
        def finder(*args, **kwargs):
            self.assertEqual(client.chat.call_count, 1)
            return {"status": "no_matching_candidate", "company_website": None}
        result = assess_job({"company": "Acme", "description": "Join Acme."}, client=client, company_finder=finder, include_trace=True)
        self.assertEqual(result["content_analysis"]["status"], "completed")
        self.assertIn("Initial content analysis", client.chat.call_args.kwargs["messages"][-2]["content"])


if __name__ == "__main__":
    unittest.main()
