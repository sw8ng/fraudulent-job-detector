import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_content import analyze_content, explicit_request_concern
from assess_job import assess_job


class ContentTests(unittest.TestCase):
    def test_explicit_request_rules_preserve_negation_and_warnings(self):
        quote = "send your bank password"
        self.assertTrue(explicit_request_concern(quote, "Please send your bank password to us"))
        self.assertFalse(explicit_request_concern(quote, "Never send your bank password"))
        self.assertFalse(explicit_request_concern(quote, "Warning: scammers ask you to send your bank password"))
        self.assertTrue(explicit_request_concern("send a $300 equipment deposit by gift card", "Before your interview send a $300 equipment deposit by gift card"))
        self.assertFalse(explicit_request_concern("hourly pay $62", "hourly pay $62"))

    def test_description_focus_and_pdf_whitespace_preserve_original_quote(self):
        client = self.client({"observations": [{"category": "payment", "quote": "We never charge fees.",
            "interpretation": "Denies applicant fees", "assessment": "context"}], "claims_to_verify": [], "limitations": []})
        result = analyze_content({"description": "We never charge fees.",
            "raw_posting_text": "Try Premium for $0\nWe never\ncharge fees."}, client=client)
        self.assertEqual(result["observations"][0]["quote"], "We never\ncharge fees.")
        self.assertEqual(result["source_field"], "description")
        self.assertNotIn("Try Premium", client.chat.call_args.kwargs["messages"][1]["content"])

    def test_negation_is_not_normalized_away(self):
        client = self.client({"observations": [{"category": "payment", "quote": "We charge fees.",
            "interpretation": "Fees", "assessment": "concern"}], "claims_to_verify": [], "limitations": []})
        result = analyze_content({"description": "We never charge fees."}, client=client)
        self.assertEqual(result["observations"], [])

    def test_extracted_quote_must_also_exist_in_original(self):
        client = self.client({"observations": [{"category": "payment", "quote": "Pay $500.",
            "interpretation": "Fees", "assessment": "concern"}], "claims_to_verify": [], "limitations": []})
        result = analyze_content({"description": "Pay $500.", "raw_posting_text": "No payment required."}, client=client)
        self.assertEqual(result["observations"], [])

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
        self.assertEqual(result["investigation"]["content_analysis"]["status"], "completed")
        self.assertTrue(any("Initial content analysis" in m.get("content", "") for m in client.chat.call_args.kwargs["messages"] if isinstance(m, dict)))


if __name__ == "__main__":
    unittest.main()
