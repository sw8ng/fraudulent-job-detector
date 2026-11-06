import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from jsonschema import ValidationError, validate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_content import CONTENT_SCHEMA, analyze_content


class ContentRepetitionTests(unittest.TestCase):
    def observation(self):
        return {"category": "salary_requirements", "quote": "$150,000 per year",
                "interpretation": "Salary observation", "assessment": "uncertain"}

    def test_generation_schema_does_not_allow_unbounded_observation_lists(self):
        data = {"observations": [self.observation()] * 40, "claims_to_verify": [], "limitations": []}
        with self.assertRaises(ValidationError):
            validate(data, CONTENT_SCHEMA)

    def test_duplicate_observations_are_not_repeated_in_final_content_evidence(self):
        data = {"observations": [self.observation(), self.observation()], "claims_to_verify": [], "limitations": []}
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason="stop", message=SimpleNamespace(content=json.dumps(data)))
        result = analyze_content({"description": "Pay: $150,000 per year."}, model="test-model", client=client)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(len(result["raw_model_output"]["observations"]), 2)
        self.assertEqual(result["duplicate_counts"]["observations"], 1)

    def test_incomplete_json_is_never_treated_as_successful_analysis(self):
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason="length", message=SimpleNamespace(content='{"observations":['))
        with self.assertRaisesRegex(ValueError, "truncated"):
            analyze_content({"description": "Posting text"}, model="test-model", client=client)
