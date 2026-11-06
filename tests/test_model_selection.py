import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assess_job import assess_job
from analyze_content import analyze_content
from extract_job import extract_job
from model_client import MeasuredClient
from performance_view import write_dashboard


def response(data):
    return SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps(data)))


class ModelSelectionTests(unittest.TestCase):
    def test_no_silent_model_default(self):
        with self.assertRaises(TypeError):
            assess_job({})

    def test_selected_model_reaches_content_and_final_calls(self):
        client = Mock()
        client.chat.side_effect = [response({"observations": [], "claims_to_verify": [], "limitations": []}),
                                   response({"risk_level": "insufficient evidence", "explanation": "Limited evidence."})]
        report = assess_job({"description": "An ordinary posting."}, model="llama3.1:8b", client=client, include_trace=True)
        self.assertTrue(all(call.kwargs["model"] == "llama3.1:8b" for call in client.chat.call_args_list))
        self.assertEqual(report["investigation"]["model"], "llama3.1:8b")
        self.assertEqual([c["stage"] for c in report["investigation"]["performance"]["model_calls"]], ["content_analysis", "final_assessment"])

    def test_extraction_passes_all_use_selected_model(self):
        def fields(text, prompt, schema, *, model):
            return {key: [] if prop["type"] == "array" else None for key, prop in schema["properties"].items()}
        with patch("extract_job._extract_fields", side_effect=fields) as extract:
            extract_job("Posting text", model="llama3.1:8b")
        self.assertEqual(extract.call_count, 3)
        self.assertTrue(all(c.kwargs["model"] == "llama3.1:8b" for c in extract.call_args_list))

    def test_raw_content_is_preserved_before_rule_corrections(self):
        client = Mock()
        client.chat.return_value = response({"observations": [{"category": "sensitive_data",
            "quote": "Never send your bank password.", "interpretation": "Risky", "assessment": "concern"}],
            "claims_to_verify": [], "limitations": []})
        result = analyze_content({"description": "Never send your bank password."}, model="llama3.1:8b", client=client)
        self.assertEqual(result["raw_model_output"]["observations"][0]["assessment"], "concern")
        self.assertEqual(result["observations"][0]["assessment"], "context")

    def test_failed_model_call_is_measured(self):
        underlying = Mock()
        underlying.chat.side_effect = RuntimeError("offline")
        client = MeasuredClient(underlying)
        with self.assertRaises(RuntimeError):
            client.chat(model="llama3.1:8b")
        self.assertEqual(client.calls[0]["status"], "failed")
        self.assertGreaterEqual(client.calls[0]["seconds"], 0)

    def test_dashboard_does_not_embed_unescaped_input_html(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comparison.html"
            write_dashboard({"runs": [], "note": "</script><script>alert(1)</script>"}, path)
            html = path.read_text()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("\\u003c/script>", html)
        self.assertIn('id="model"', html)
