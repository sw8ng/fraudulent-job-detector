import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class Item(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self).copy()


class OpenAIAdapterTests(unittest.TestCase):
    def test_responses_tool_history_retains_reasoning_and_call_ids(self):
        from provider_clients import OpenAIClient
        sdk = Mock()
        sdk.responses.create.side_effect = [SimpleNamespace(
            output=[Item(type='reasoning', id='rs_test', encrypted_content='opaque'),
                    Item(type='function_call', call_id='call_1', name='inspect_application_url', arguments='{}')],
            output_text='', status='completed', model='gpt-6.2-sol', id='resp_test',
            usage=SimpleNamespace(input_tokens=50, output_tokens=20)),
            SimpleNamespace(output=[], output_text='Done', status='completed',
                model='gpt-6.2-sol', id='resp_done', usage=SimpleNamespace(input_tokens=70,output_tokens=5))]
        client = OpenAIClient(client=sdk)
        messages = [{'role':'system','content':'Use evidence'}, {'role':'user','content':'Posting'}]
        first = client.chat(model='gpt-6.2-sol',messages=messages,options={'num_predict':1024,'temperature':0})
        messages.extend([first.message, {'role':'tool','tool_name':'inspect_application_url','content':'{"http_status":200}'},
                         {'role':'user','content':'Next round'}])
        client.chat(model='gpt-6.2-sol',messages=messages,options={'num_predict':1024})
        request = sdk.responses.create.call_args.kwargs
        self.assertEqual(request['input'][1]['type'],'reasoning')
        self.assertEqual(request['input'][3]['call_id'],'call_1')
        self.assertEqual(request['reasoning'],{'effort':'low'})
        self.assertNotIn('temperature',request)
        self.assertFalse(request['store'])

    def test_structured_choices_use_strict_responses_format(self):
        from provider_clients import OpenAIClient
        sdk=Mock()
        sdk.responses.create.return_value=SimpleNamespace(output=[],output_text='{"findings":[]}',status='completed',
            usage=SimpleNamespace(input_tokens=10,output_tokens=5),model='gpt-6.2-sol',id='resp_test')
        schema={'type':'object','properties':{'findings':{'type':'array','maxItems':0}},'required':['findings'],'additionalProperties':False}
        result=OpenAIClient(client=sdk).chat(model='gpt-6.2-sol',messages=[{'role':'user','content':'Assess'}],format=schema)
        request=sdk.responses.create.call_args.kwargs
        self.assertTrue(request['text']['format']['strict'])
        self.assertEqual(json.loads(result.message.content),{'findings':[]})

    def test_cloud_only_preflight_and_local_gpt_oss_routing(self):
        from compare_models import preflight_models
        from model_client import provider_for_model
        self.assertEqual(provider_for_model('gpt-oss:20b'),'ollama')
        with patch('compare_models.Client',side_effect=AssertionError('No Ollama')), \
             patch('compare_models.preflight_openai_model') as preflight:
            preflight_models(['gpt-6.2-sol'])
        preflight.assert_called_once_with('gpt-6.2-sol')

    def test_failed_requests_are_sanitized(self):
        from provider_clients import OpenAIClient,ModelProviderError
        sdk=Mock();sdk.responses.create.side_effect=RuntimeError('PRIVATE_OPENAI_TEST_KEY')
        with self.assertRaises(ModelProviderError) as error:
            OpenAIClient(client=sdk).chat(model='gpt-6.2-sol',messages=[{'role':'user','content':'Test'}])
        self.assertNotIn('PRIVATE_OPENAI_TEST_KEY',str(error.exception))
