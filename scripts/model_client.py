"""Record local model latency and raw responses without changing model settings."""

import json
import time


class MeasuredClient:
    """Small wrapper so every assessment model call is measured the same way."""

    def __init__(self, client):
        self.client = client
        self.calls = []
        self.stage = "unspecified"

    def chat(self, **kwargs):
        started = time.perf_counter()
        record = {"stage": self.stage, "model": kwargs["model"],
                  "options": kwargs.get("options", {}), "status": "failed"}
        try:
            response = self.client.chat(**kwargs)
            record["status"] = "completed"
            for key in ("eval_count", "prompt_eval_count", "eval_duration", "prompt_eval_duration", "load_duration"):
                value = getattr(response, key, None)
                if isinstance(value, (int, float)):
                    record[key] = value
            text = getattr(response.message, "content", None)
            if isinstance(text, str):
                try:
                    record["raw_response"] = json.loads(text)
                except ValueError:
                    record["raw_response"] = text
            requests = getattr(response.message, "tool_calls", None) or []
            record["raw_tool_calls"] = []
            for call in requests:
                request = {
                    "name": call.function.name,
                    "arguments": dict(call.function.arguments),
                }
                record["raw_tool_calls"].append(request)
            record["done_reason"] = getattr(response, "done_reason", None)
            return response
        except Exception as error:
            record["error"] = type(error).__name__ + ": " + str(error)
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - started, 4)
            self.calls.append(record)
