import copy
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jsonschema import validate, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


def reply(content, stop='end_turn'):
    return SimpleNamespace(content=content, stop_reason=stop, id='msg_test', model='claude-sonnet-5-5',
        usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0))


class AnthropicAdapterTests(unittest.TestCase):
    def test_nullable_website_enum_survives_sdk_schema_transform(self):
        from provider_clients import claude_schema
        schema={'type':'object','properties':{'selected_url':{'type':['string','null'],
            'enum':['https://example.com/',None]}},'required':['selected_url'],'additionalProperties':False}
        transformed=claude_schema(schema)
        validate({'selected_url':None},transformed)
        validate({'selected_url':'https://example.com/'},transformed)
        with self.assertRaises(ValidationError):validate({'selected_url':'https://unlisted.example/'},transformed)

    def test_request_uses_supported_sdk_parameters(self):
        import inspect
        from anthropic.resources.messages import Messages
        from provider_clients import AnthropicClient
        sdk=Mock();sdk.messages.create.return_value=reply([SimpleNamespace(type='text',text='Done')])
        AnthropicClient(client=sdk).chat(model='claude-sonnet-5-5',messages=[{'role':'user','content':'Test'}])
        allowed=set(inspect.signature(Messages.create).parameters)
        self.assertTrue(set(sdk.messages.create.call_args.kwargs).issubset(allowed))

    def test_tool_ids_survive_two_tool_results_and_next_prompt(self):
        from provider_clients import AnthropicClient
        sdk = Mock()
        sdk.messages.create.side_effect = [reply([
            SimpleNamespace(type='tool_use', id='tool_a', name='inspect_application_url', input={}),
            SimpleNamespace(type='tool_use', id='tool_b', name='get_company_domain_info', input={})], 'tool_use'),
            reply([SimpleNamespace(type='text', text='No more checks.')])]
        adapter = AnthropicClient(client=sdk)
        messages = [{'role': 'system', 'content': 'Use evidence.'}, {'role': 'user', 'content': 'Posting'}]
        tools = [{'type': 'function', 'function': {'name': 'inspect_application_url',
                 'description': 'Inspect stored URL', 'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
        first = adapter.chat(model='claude-sonnet-5-5', messages=messages, tools=tools,
                             options={'num_predict': 1024, 'temperature': 0})
        messages += [first.message,
            {'role': 'tool', 'tool_name': 'inspect_application_url', 'content': '{"http_status":200}'},
            {'role': 'tool', 'tool_name': 'get_company_domain_info', 'content': '{"lookup_success":true}'},
            {'role': 'user', 'content': 'Next round'}]
        adapter.chat(model='claude-sonnet-5-5', messages=messages, tools=tools, options={'num_predict': 1024})
        sent = sdk.messages.create.call_args.kwargs
        self.assertEqual(sent['system'], 'Use evidence.')
        self.assertEqual([b['tool_use_id'] for b in sent['messages'][-1]['content'] if b['type']=='tool_result'], ['tool_a', 'tool_b'])
        self.assertEqual(sent['messages'][-1]['content'][-1]['text'], 'Next round')
        self.assertEqual(sent['tools'][0]['input_schema']['properties'], {})
        self.assertEqual(sent['tool_choice'], {'type': 'auto'})

    def test_object_enum_is_translated_without_losing_choice_constraints(self):
        from provider_clients import claude_schema
        choice = {'id': 'no_http_redirects', 'quote': '', 'evidence_ids': ['url-1']}
        schema = {'type': 'object', 'properties': {'findings': {'type': 'array', 'maxItems': 1,
            'uniqueItems': True, 'items': {'enum': [choice]}}}, 'required': ['findings'], 'additionalProperties': False}
        before = copy.deepcopy(schema)
        translated = claude_schema(schema)
        validate({'findings': [choice]}, translated)
        with self.assertRaises(ValidationError):
            validate({'findings': [dict(choice, evidence_ids=['invented'])]}, translated)
        self.assertEqual(schema, before)

    def test_usage_and_truncation_are_native_not_fake_generation_timing(self):
        from provider_clients import AnthropicClient
        from model_client import MeasuredClient
        sdk = Mock()
        sdk.messages.create.return_value = reply([SimpleNamespace(type='text', text='{"x":')], 'max_tokens')
        client = MeasuredClient(AnthropicClient(client=sdk))
        result = client.chat(model='claude-sonnet-5-5', messages=[{'role':'user','content':'Test'}],
                             format={'type':'object','properties':{'x':{'type':'string'}},'required':['x'],'additionalProperties':False},
                             options={'num_predict':1024,'num_ctx':32768,'temperature':0}, think=False)
        self.assertEqual(result.done_reason, 'length')
        self.assertEqual(client.calls[0]['prompt_eval_count'], 100)
        self.assertEqual(client.calls[0]['eval_count'], 20)
        self.assertNotIn('eval_duration', client.calls[0])
        self.assertEqual(client.calls[0]['provider'], 'anthropic')
        self.assertAlmostEqual(client.calls[0]['estimated_api_cost_usd'], .0004)
        self.assertEqual(sdk.messages.create.call_args.kwargs['max_tokens'], 1024)

    def test_provider_failure_never_echoes_credentials_or_error_body(self):
        from provider_clients import AnthropicClient, ModelProviderError
        sdk = Mock()
        sdk.messages.create.side_effect = RuntimeError('Authorization: PRIVATE_TEST_SECRET')
        with self.assertRaises(ModelProviderError) as caught:
            AnthropicClient(client=sdk).chat(model='claude-sonnet-5-5', messages=[{'role':'user','content':'Test'}])
        self.assertNotIn('PRIVATE_TEST_SECRET', str(caught.exception))

    def test_cloud_only_preflight_does_not_contact_ollama(self):
        from compare_models import preflight_models
        with patch('compare_models.Client', side_effect=AssertionError('No local model')), \
             patch('compare_models.preflight_claude_model') as cloud:
            preflight_models(['claude-sonnet-5-5'])
        cloud.assert_called_once_with('claude-sonnet-5-5')


if __name__ == '__main__':
    unittest.main()
