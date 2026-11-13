import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from performance_view import timing_parts


class TimingOrderTests(unittest.TestCase):
    def test_tools_follow_the_selection_that_requested_them(self):
        run = {"seconds": 10, "model_calls": [
            {"stage": "content_analysis", "seconds": 1},
            {"stage": "tool_selection", "seconds": 1},
            {"stage": "tool_selection", "seconds": 1},
            {"stage": "final_assessment", "seconds": 1}]}
        tools = [{"tool": "inspect_application_url", "round": 1, "seconds": 2},
                 {"tool": "collect_application_page", "round": 2, "seconds": 2}]
        parts = timing_parts(run, tools)
        self.assertEqual([p["name"] for p in parts], ["content_analysis", "tool_selection",
            "inspect_application_url", "tool_selection", "collect_application_page", "final_assessment", "unmeasured"])
        self.assertEqual(sum(p["seconds"] for p in parts), 10)

    def test_older_requests_without_rounds_are_matched(self):
        run = {"seconds": 4, "model_calls": [
            {"stage": "tool_selection", "seconds": 1, "raw_tool_calls": [{"name": "inspect_application_url"}]},
            {"stage": "final_assessment", "seconds": 1}]}
        parts = timing_parts(run, [{"tool": "inspect_application_url", "seconds": 2}])
        self.assertEqual([p["name"] for p in parts], ["tool_selection", "inspect_application_url", "final_assessment"])

    def test_unknown_order_is_explicit_and_not_dropped(self):
        parts = timing_parts({"seconds": 3}, [{"tool": "inspect_application_url", "seconds": 1}])
        self.assertIn("order unrecorded", parts[0]["label"])
        self.assertIn("Position Unrecorded", parts[1]["label"])
