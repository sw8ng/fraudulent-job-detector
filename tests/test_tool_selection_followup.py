import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from assess_job import assess_job


def response(data=None, calls=None):
    return SimpleNamespace(done_reason="stop", message=SimpleNamespace(
        content=json.dumps(data) if data is not None else "Not enough evidence.", tool_calls=calls))


class ToolSelectionFollowupTests(unittest.TestCase):
    def test_early_stop_gets_one_bounded_chance_to_use_available_evidence(self):
        call = SimpleNamespace(function=SimpleNamespace(name="inspect_application_url",
            arguments={"application_url": "https://example.com/apply"}))
        client = Mock()
        client.chat.side_effect = [
            response({"observations": [], "claims_to_verify": [], "limitations": []}),
            response(), response(calls=[call]),
            response({"risk_level": "insufficient evidence", "explanation": "Employer identity remains unknown."}),
        ]
        investigator = Mock(return_value={"status": "completed", "redirects": []})
        report = assess_job({"description": "An engineering role.", "application_url": "https://example.com/apply"},
            model="llama3.1:8b", client=client, investigator=investigator, include_trace=True)
        investigator.assert_called_once_with("https://example.com/apply", None)
        self.assertEqual(len(report["investigation"]["tool_calls"]), 1)
        stages = [call["stage"] for call in report["investigation"]["performance"]["model_calls"]]
        self.assertEqual(stages.count("tool_selection"), 2)

    def test_model_can_still_decline_tools_after_followup(self):
        client = Mock()
        client.chat.side_effect = [
            response({"observations": [], "claims_to_verify": [], "limitations": []}),
            response(), response(),
            response({"risk_level": "insufficient evidence", "explanation": "Evidence remains limited."}),
        ]
        investigator = Mock()
        report = assess_job({"description": "A role.", "application_url": "https://example.com/apply"},
            model="llama3.1:8b", client=client, investigator=investigator, include_trace=True)
        investigator.assert_not_called()
        self.assertEqual(report["investigation"]["tool_calls"], [])
        self.assertEqual(report["assessment"]["risk_level"], "insufficient evidence")
