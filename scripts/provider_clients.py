"""Small cloud adapters for the existing chat/tool interface; no assessment policy."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace


class ModelProviderError(ValueError):
    """A safe provider failure without SDK request bodies or credentials."""


def field(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def load_model_environment():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)


def provider_error(provider, error):
    code = getattr(error, 'status_code', None)
    return ModelProviderError(provider + ' request failed (' + type(error).__name__ +
                              (', HTTP ' + str(code) if isinstance(code, int) else '') + ').')


def literal_schema(value):
    if isinstance(value, dict):
        return {'type': 'object', 'properties': {k: literal_schema(v) for k, v in value.items()},
                'required': list(value), 'additionalProperties': False}
    if isinstance(value, list):
        return {'type': 'array', 'items': literal_schema(value[0]) if value else {'type': 'string'},
                'minItems': len(value), 'maxItems': len(value)}
    kind = 'null' if value is None else 'boolean' if isinstance(value, bool) else 'string' if isinstance(value, str) else 'number'
    return {'type': kind, 'enum': [value]}


def normalized_schema(schema):
    """Express object-valued enums as equivalent supported object alternatives."""
    if isinstance(schema, list):
        return [normalized_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if isinstance(schema.get('type'), list):
        alternatives = []
        for kind in schema['type']:
            branch = {key: copy.deepcopy(value) for key, value in schema.items() if key not in ('type', 'enum')}
            branch['type'] = kind
            if 'enum' in schema:
                allowed = [v for v in schema['enum'] if (kind == 'null' and v is None) or
                           (kind == 'string' and isinstance(v, str)) or
                           (kind == 'boolean' and isinstance(v, bool)) or
                           (kind in ('integer', 'number') and isinstance(v, (int, float)) and not isinstance(v, bool))]
                if not allowed:
                    continue
                branch['enum'] = allowed
            alternatives.append(normalized_schema(branch))
        return {'anyOf': alternatives}
    values = schema.get('enum', [])
    if any(isinstance(v, (dict, list)) for v in values):
        return {'anyOf': [literal_schema(value) for value in values]}
    result = {key: normalized_schema(value) for key, value in schema.items()}
    if result.get('type') == 'array' and 'items' not in result:
        result['items'] = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
    if 'oneOf' in result:
        result['anyOf'] = result.pop('oneOf')
    return result


def claude_schema(schema):
    from anthropic import transform_schema
    return transform_schema(normalized_schema(copy.deepcopy(schema)))


def openai_schema(schema):
    result = normalized_schema(copy.deepcopy(schema))
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            # Uniqueness is also checked by the common final-assessment validator.
            node.pop('uniqueItems', None)
            if node.get('type') == 'object':
                node['additionalProperties'] = False
                node['required'] = list(node.get('properties', {}))
            for child in list(node.values()):
                visit(child)
    visit(result)
    return result


def tool_result_id(pending, name):
    for index, (identifier, requested_name) in enumerate(pending):
        if requested_name == name:
            pending.pop(index)
            return identifier
    raise ModelProviderError('Tool response has no matching model tool-call ID.')


def claude_messages(messages):
    system, converted, pending = [], [], []
    for message in messages:
        role, content = field(message, 'role'), field(message, 'content') or ''
        if role == 'system':
            system.append(content)
            continue
        blocks = []
        if role == 'tool':
            blocks.append({'type': 'tool_result', 'tool_use_id': tool_result_id(pending, field(message, 'tool_name')),
                           'content': content})
            role = 'user'
        else:
            native = field(message, 'provider_items')
            if native:
                blocks.extend(copy.deepcopy(native))
            elif content:
                blocks.append({'type': 'text', 'text': content})
            for call in field(message, 'tool_calls', []) or []:
                function = field(call, 'function')
                identifier = field(call, 'id')
                if not identifier:
                    raise ModelProviderError('Claude tool call is missing its ID.')
                name = field(function, 'name')
                pending.append((identifier, name))
                if not native:
                    blocks.append({'type': 'tool_use', 'id': identifier, 'name': name, 'input': field(function, 'arguments', {})})
        if blocks:
            if converted and converted[-1]['role'] == role:
                converted[-1]['content'].extend(blocks)
            else:
                converted.append({'role': role, 'content': blocks})
    return '\n\n'.join(system), converted


def openai_messages(messages):
    system, converted, pending = [], [], []
    for message in messages:
        role, content = field(message, 'role'), field(message, 'content') or ''
        if role == 'system':
            system.append(content)
        elif role == 'tool':
            converted.append({'type': 'function_call_output',
                'call_id': tool_result_id(pending, field(message, 'tool_name')), 'output': content})
        else:
            native = field(message, 'provider_items')
            if native:
                converted.extend(copy.deepcopy(native))
            elif content:
                converted.append({'role': role, 'content': content})
            for call in field(message, 'tool_calls', []) or []:
                function = field(call, 'function')
                identifier = field(call, 'id')
                pending.append((identifier, field(function, 'name')))
                if not native:
                    converted.append({'type': 'function_call', 'call_id': identifier,
                        'name': field(function, 'name'), 'arguments': json.dumps(field(function, 'arguments', {}))})
    return '\n\n'.join(system), converted


def normalized_response(text, calls, reason, usage, provider, model, response_id, native=None):
    """Keep genuine token counters; APIs do not supply Ollama generation durations."""
    input_tokens = field(usage, 'input_tokens', 0)
    output_tokens = field(usage, 'output_tokens', 0)
    cache_read = field(usage, 'cache_read_input_tokens', 0) or 0
    cache_write = field(usage, 'cache_creation_input_tokens', 0) or 0
    cached_openai = field(field(usage, 'input_tokens_details', {}), 'cached_tokens', 0) or 0
    pricing = {'claude-sonnet-5-5': (2, 10), 'gpt-6.2-sol': (2, 10)}
    estimate = None
    if model in pricing and not (cache_read or cache_write or cached_openai):
        rates = pricing[model]
        estimate = (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000
    return SimpleNamespace(message=SimpleNamespace(role='assistant', content=text, tool_calls=calls,
        provider_items=native), done_reason=reason, prompt_eval_count=input_tokens, eval_count=output_tokens,
        provider=provider, provider_model=model, provider_response_id=response_id,
        provider_usage={'input_tokens': input_tokens, 'output_tokens': output_tokens,
                        'cache_read_input_tokens': cache_read, 'cache_creation_input_tokens': cache_write,
                        'cached_input_tokens': cached_openai,
                        'reasoning_tokens': field(field(usage, 'output_tokens_details', {}), 'reasoning_tokens', None)},
        estimated_api_cost_usd=estimate,
        pricing_note='Estimate from standard uncached rates checked 2026-11-20; not an invoice. Cache-priced requests remain unestimated.')


class AnthropicClient:
    def __init__(self, *, client=None, timeout=300):
        if client is None:
            from anthropic import Anthropic
            load_model_environment()
            if not os.environ.get('ANTHROPIC_API_KEY'):
                raise ModelProviderError('Set ANTHROPIC_API_KEY in the environment or ignored .env file.')
            client = Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'], timeout=timeout, max_retries=0,
                               base_url='https://api.anthropic.com')
        self.client = client

    def preflight(self, model):
        try:
            return self.client.models.retrieve(model, timeout=10)
        except Exception as error:
            raise provider_error('Claude', error) from None

    def chat(self, *, model, messages, tools=None, format=None, options=None, think=False):
        options = options or {}
        system, converted = claude_messages(messages)
        request = {'model': model, 'system': system, 'messages': converted,
                   'max_tokens': options.get('num_predict', 1024),
                   'thinking': {'type': 'between_tools'}}
        if tools:
            request['tools'] = [{'name': t['function']['name'], 'description': t['function']['description'],
                'input_schema': claude_schema(t['function']['parameters']), 'strict': True} for t in tools]
            request['tool_choice'] = {'type': 'auto'}
        if format:
            request['output_config'] = {'format': {'type': 'json_schema', 'schema': claude_schema(format)}}
        try:
            response = self.client.messages.create(**request)
        except Exception as error:
            raise provider_error('Claude', error) from None
        text, calls, native = [], [], []
        for block in response.content:
            if hasattr(block, 'model_dump'):
                native.append(block.model_dump(mode='json', exclude_none=True))
            else:
                native.append(vars(block).copy())
            if block.type == 'text':
                text.append(block.text)
            elif block.type == 'tool_use':
                calls.append(SimpleNamespace(id=block.id, function=SimpleNamespace(name=block.name, arguments=block.input)))
        if response.stop_reason == 'refusal':
            raise ModelProviderError('Claude refused the request; no verdict was produced.')
        result = normalized_response(''.join(text), calls, 'length' if response.stop_reason == 'max_tokens' else 'stop',
            response.usage, 'anthropic', response.model, response.id, native)
        result.provider_options = {'max_tokens': request['max_tokens'], 'sampling': 'Provider-controlled; no SDK temperature parameter',
                                   'thinking': request['thinking'], 'automatic_retries': 0}
        return result


class OpenAIClient:
    def __init__(self, *, client=None, timeout=300):
        if client is None:
            from openai import OpenAI
            load_model_environment()
            if not os.environ.get('OPENAI_API_KEY'):
                raise ModelProviderError('Set OPENAI_API_KEY in the environment or ignored .env file.')
            client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=timeout, max_retries=0,
                            base_url='https://api.openai.com/v1')
        self.client = client

    def preflight(self, model):
        try:
            return self.client.models.retrieve(model, timeout=10)
        except Exception as error:
            raise provider_error('OpenAI', error) from None

    def chat(self, *, model, messages, tools=None, format=None, options=None, think=False):
        options = options or {}
        instructions, converted = openai_messages(messages)
        # GPT-6.2 requires reasoning; it does not support effort=none or temperature.
        request = {'model': model, 'instructions': instructions, 'input': converted, 'store': False,
                   'reasoning': {'effort': 'low'}, 'include': ['reasoning.encrypted_content'],
                   'max_output_tokens': options.get('num_predict', 1024)}
        if tools:
            request['tools'] = [{'type': 'function', 'name': t['function']['name'],
                'description': t['function']['description'], 'parameters': openai_schema(t['function']['parameters']),
                'strict': True} for t in tools]
            request['tool_choice'] = 'auto'
        if format:
            request['text'] = {'format': {'type': 'json_schema', 'name': 'assessment_output',
                                          'strict': True, 'schema': openai_schema(format)}}
        try:
            response = self.client.responses.create(**request)
        except Exception as error:
            raise provider_error('OpenAI', error) from None
        calls, native = [], []
        for item in response.output:
            native.append(item.model_dump(mode='json', exclude_none=True))
            if item.type == 'function_call':
                calls.append(SimpleNamespace(id=item.call_id, function=SimpleNamespace(
                    name=item.name, arguments=json.loads(item.arguments))))
            elif item.type == 'message' and any(block.type == 'refusal' for block in item.content):
                raise ModelProviderError('OpenAI refused the request; no verdict was produced.')
        if response.status not in ('completed', 'incomplete'):
            raise ModelProviderError('OpenAI response did not complete; no verdict was produced.')
        result = normalized_response(response.output_text, calls, 'length' if response.status == 'incomplete' else 'stop',
            response.usage, 'openai', response.model, response.id, native)
        result.provider_options = {'max_output_tokens': request['max_output_tokens'], 'reasoning': request['reasoning'],
                                   'store': False, 'automatic_retries': 0}
        return result
