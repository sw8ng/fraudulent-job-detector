import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from assess_job import assess_job


def response(data=None, name=None, arguments=None):
    calls = []
    if name:
        calls.append(SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments or {})))
    return SimpleNamespace(done_reason='stop', message=SimpleNamespace(
        content=json.dumps(data) if data is not None else '', tool_calls=calls))


class SharedWorkflowTests(unittest.TestCase):
    job = {'description':'Engineer role.', 'application_url':'https://example.com/apply?long=source-value',
           'company_website':'https://example.com/', 'job_title':'Engineer'}

    def run_sequence(self, requests, check_url_schema=False):
        count = 0
        def chat(**kwargs):
            nonlocal count
            if 'tools' in kwargs:
                if len(self.job['application_url']) > 160:
                    self.assertNotIn(self.job['application_url'], str(kwargs['messages']))
                if check_url_schema:
                    for tool in kwargs['tools']:
                        if isinstance(tool, dict) and tool['function']['name']=='inspect_application_url':
                            self.assertEqual(tool['function']['parameters']['properties'], {})
                            break
                    else:
                        if count == 0:
                            self.fail('URL Inspection should be advertised without URL arguments')
                request = requests[count]
                count += 1
                return response(name=request[0], arguments=request[1])
            if 'observations' in kwargs['format']['properties']:
                return response({'observations':[], 'claims_to_verify':[], 'limitations':[]})
            return response({'risk_level':'insufficient evidence', 'explanation':'Identity remains unverified.'})
        client = Mock()
        client.chat.side_effect = chat
        investigator = Mock(return_value={'http_status':200,'redirect_lookup_success':True,
            'final_url':'https://example.com/form','redirects':[]})
        with patch('assess_job.check_employer_association', return_value={'status':'not_found_in_checked_pages'}), \
             patch('assess_job.collect_application_page', return_value={'status':'collected','extraction_success':True,
                 'final_url':'https://example.com/form','text':'Apply here.'}) as collector:
            report = assess_job(self.job, model='any-local-model', client=client,
                                investigator=investigator, include_trace=True)
        return report, investigator, collector, count

    def test_url_is_bound_by_python_instead_of_retyped_by_model(self):
        report, inspector, collector, count = self.run_sequence([
            ('inspect_application_url', {}),
            ('collect_application_page', {}),
            ('check_employer_association', {'company_website':'https://example.com/','job_title':'Engineer'})
        ], check_url_schema=True)
        inspector.assert_called_once_with(self.job['application_url'], self.job['company_website'])
        self.assertEqual(report['investigation']['tool_calls'][0]['effective_arguments']['application_url'], self.job['application_url'])

    def test_sequential_choices_can_reach_collection_in_a_bounded_third_round(self):
        report, inspector, collector, count = self.run_sequence([
            ('inspect_application_url', {'application_url':self.job['application_url']}),
            ('check_employer_association', {'company_website':'https://example.com/','job_title':'Engineer'}),
            ('collect_application_page', {})
        ])
        collector.assert_called_once_with('https://example.com/form')
        self.assertEqual(count, 3)
        self.assertEqual(report['investigation']['tool_calls'][-1]['round'], 3)

    def test_third_opportunity_does_not_force_collection(self):
        report, inspector, collector, count = self.run_sequence([
            ('inspect_application_url', {}),
            ('check_employer_association', {'company_website':'https://example.com/','job_title':'Engineer'}),
            (None, {})
        ])
        collector.assert_not_called()
        self.assertEqual(count, 3)
        self.assertEqual(report['assessment']['risk_level'], 'insufficient evidence')

    def test_redundant_source_url_is_validated_but_destination_stays_bound(self):
        report, inspector, collector, count = self.run_sequence([
            ('inspect_application_url', {}),
            ('collect_application_page', {'application_url':self.job['application_url']}),
            ('check_employer_association', {'company_website':'https://example.com/','job_title':'Engineer'})
        ])
        collector.assert_called_once_with('https://example.com/form')
        self.assertEqual(report['investigation']['tool_calls'][1]['arguments']['application_url'], self.job['application_url'])
        self.assertEqual(report['investigation']['tool_calls'][1]['effective_arguments']['final_url'], 'https://example.com/form')


    def test_long_url_is_shortened_for_selection_but_execution_is_exact(self):
        self.job = dict(self.job, application_url='https://example.com/apply?tracking=' + 'x' * 2000)
        report, inspector, collector, count = self.run_sequence([
            ('inspect_application_url', {}),
            ('collect_application_page', {}),
            ('check_employer_association', {})
        ])
        inspector.assert_called_once_with(self.job['application_url'], self.job['company_website'])
        self.assertEqual(report['investigation']['tool_calls'][0]['effective_arguments']['application_url'], self.job['application_url'])

    def test_association_accepts_source_bound_empty_arguments(self):
        report, inspector, collector, count = self.run_sequence([
            ('inspect_application_url', {}),
            ('collect_application_page', {}),
            ('check_employer_association', {})
        ])
        entry = report['investigation']['tool_calls'][-1]
        self.assertNotIn('error', entry['evidence'])
        self.assertEqual(entry['effective_arguments']['job_title'], 'Engineer')


class SelectionContextTests(unittest.TestCase):
    def test_tracking_query_removed_only_from_model_view(self):
        from assess_job import compact_model_messages
        url = "https://company.com/apply?token=" + "a" * 2000
        original = [{"role": "user", "content": json.dumps({"application_url": url, "job_title": "Engineer"})}]
        compact = compact_model_messages(original)
        self.assertNotIn("a" * 2000, compact[0]["content"])
        self.assertIn("https://company.com/apply", compact[0]["content"])
        self.assertIn("Engineer", compact[0]["content"])
        self.assertIn(url, original[0]["content"])

    def test_short_job_id_urls_stay_intact(self):
        from assess_job import compact_model_messages
        messages = [{"role": "tool", "content": "https://company.com/jobs?id=123"}]
        self.assertEqual(compact_model_messages(messages), messages)
