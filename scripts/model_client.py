"""Route assessment providers and record latency, native usage and raw responses."""

import json
import time
import os


def provider_for_model(model):
    if ':' not in model and model.startswith('claude-'):
        return 'anthropic'
    if ':' not in model and model.startswith('gpt-'):
        return 'openai'
    return 'ollama'


def create_model_client(model, *, timeout=300):
    provider = provider_for_model(model)
    if provider == 'anthropic':
        from provider_clients import AnthropicClient
        return AnthropicClient(timeout=timeout)
    if provider == 'openai':
        from provider_clients import OpenAIClient
        return OpenAIClient(timeout=timeout)
    from ollama import Client
    return Client(host=os.environ.get('OLLAMA_HOST', 'http://localhost:11434'), timeout=timeout)


class MeasuredClient:
    """Small wrapper so every assessment model call is measured the same way."""

    def __init__(self, client):
        self.client = client
        self.calls = []
        self.stage = "unspecified"
        self.on_progress = None

    def chat(self, **kwargs):
        started = time.perf_counter()
        record = {"stage": self.stage, "model": kwargs["model"],
                  'provider': provider_for_model(kwargs['model']),
                  "options": kwargs.get("options", {}), "status": "running",
                  "started_at": time.time(), "seconds": 0}
        self.calls.append(record)
        if self.on_progress:
            self.on_progress()
        try:
            response = self.client.chat(**kwargs)
            record["status"] = "completed"
            for key in ("eval_count", "prompt_eval_count", "eval_duration", "prompt_eval_duration", "load_duration"):
                value = getattr(response, key, None)
                if isinstance(value, (int, float)):
                    record[key] = value
            for key in ('provider', 'provider_model', 'provider_response_id', 'provider_usage',
                        'estimated_api_cost_usd', 'pricing_note', 'provider_options'):
                value = getattr(response, key, None)
                if isinstance(value, (str, dict, int, float)):
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
            record["status"] = "failed"
            record["error"] = type(error).__name__ + ": " + str(error)
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - started, 4)
            if self.on_progress:
                self.on_progress()
