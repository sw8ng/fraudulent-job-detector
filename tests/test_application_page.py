import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_jobs import prepare_html, prepare_job
from collect_application_page import collect_application_page
from assess_job import assess_job
from summary_grounding import observed_facts, summary_issues, factual_summary


class SharedHtmlTests(unittest.TestCase):
    def test_saved_and_downloaded_html_use_the_same_cleanup(self):
        html = '<nav>Menu</nav><script>ignore()</script><style>CSS</style><h1>Engineer</h1><label>Resume</label><a href="https://example.com/apply">Apply</a><footer>Footer</footer>'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'posting.html'
            path.write_text(html, encoding='utf-8')
            self.assertEqual(prepare_job(path), prepare_html(html))
        self.assertEqual(prepare_html(html), 'Engineer\nResume\nApply\n(https://example.com/apply)')

    def test_downloaded_links_resolve_against_observed_page_url(self):
        text = prepare_html('<a href="../apply?id=1">Apply</a><a href="javascript:bad()">Ignore</a>',
                            base_url='https://example.com/jobs/123')
        self.assertIn('https://example.com/apply?id=1', text)
        self.assertNotIn('javascript:', text)

    def test_empty_html_still_raises(self):
        with self.assertRaises(ValueError):
            prepare_html('<script>onlyJavascript()</script>')


class CollectorTests(unittest.TestCase):
    def collect(self, handler):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch('collect_application_page.validate_public_url'):
                return collect_application_page('https://example.com/apply', client=client)

    def test_collects_metadata_form_labels_and_links_without_a_verdict(self):
        result = self.collect(lambda request: httpx.Response(200, headers={'content-type':'text/html'},
            text='<title>Application</title><script>bad()</script><label>Resume</label><p>Pay a gift-card deposit.</p><a href="/next">Next</a>'))
        self.assertTrue(result['extraction_success'])
        self.assertEqual(result['page_title'], 'Application')
        self.assertEqual(result['http_status'], 200)
        self.assertIn('Resume', result['text'])
        self.assertIn('Pay a gift-card deposit.', result['text'])
        self.assertIn('https://example.com/next', result['text'])
        self.assertNotIn('bad()', result['text'])
        self.assertNotIn('risk_level', result)

    def test_http_and_non_html_failures(self):
        for status, media, reason in [(403,'text/html','http_error'), (404,'text/html','http_error'),
                                      (200,'application/pdf','non_html_response')]:
            with self.subTest(status=status, media=media):
                result = self.collect(lambda request: httpx.Response(status, headers={'content-type':media}, text='Not evidence'))
                self.assertEqual(result['reason'], reason)
                self.assertEqual(result['http_status'], status)
                self.assertFalse(result['extraction_success'])
                self.assertEqual(result['text'], '')

    def test_empty_page_and_timeout(self):
        result = self.collect(lambda request: httpx.Response(200, headers={'content-type':'text/html'}, text='<script>render()</script>'))
        self.assertEqual(result['reason'], 'empty_page_text')
        def timeout(request):
            raise httpx.ReadTimeout('Timeout', request=request)
        self.assertEqual(self.collect(timeout)['reason'], 'timeout')

    def test_text_and_download_are_bounded(self):
        result = self.collect(lambda request: httpx.Response(200, headers={'content-type':'text/html'}, text='<p>' + 'x' * 13000 + '</p>'))
        self.assertEqual(len(result['text']), 12000)
        self.assertTrue(result['text_truncated'])
        result = self.collect(lambda request: httpx.Response(200, headers={'content-type':'text/html'}, content=b'x' * 2_000_001))
        self.assertEqual(result['reason'], 'fetch_failed')
        self.assertEqual(result['text'], '')

    def test_changed_destination_records_new_redirect_and_uses_only_get(self):
        requests = []
        def handler(request):
            requests.append((request.method, str(request.url)))
            if request.url.path == '/apply':
                return httpx.Response(302, headers={'location':'/form'})
            return httpx.Response(200, headers={'content-type':'text/html'}, text='<label>Name</label>')
        result = self.collect(handler)
        self.assertEqual(result['requested_url'], 'https://example.com/apply')
        self.assertEqual(result['final_url'], 'https://example.com/form')
        self.assertEqual(requests, [('GET','https://example.com/apply'),('GET','https://example.com/form')])

    def test_private_redirect_is_not_fetched(self):
        requested = []
        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={'location':'http://127.0.0.1/admin'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch('collect_application_page.validate_public_url', side_effect=[None, ValueError('Private destination')]):
                result = collect_application_page('https://example.com/apply', client=client)
        self.assertEqual(requested, ['https://example.com/apply'])
        self.assertEqual(result['reason'], 'fetch_failed')

    def test_loop_missing_location_and_redirect_limit(self):
        for location in ('/apply', None):
            headers = {'location':location} if location else {}
            result = self.collect(lambda request: httpx.Response(302, headers=headers))
            self.assertEqual(result['reason'], 'fetch_failed')
        requested = []
        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={'location':'/hop' + str(len(requested))})
        result = self.collect(handler)
        self.assertEqual(len(requested), 5)
        self.assertEqual(result['reason'], 'fetch_failed')


def response(data=None, name=None, arguments=None):
    calls = []
    if name:
        calls.append(SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments or {})))
    return SimpleNamespace(done_reason='stop', message=SimpleNamespace(content=json.dumps(data) if data is not None else '', tool_calls=calls))


class AgentCollectorTests(unittest.TestCase):
    def run_agent(self, model, http_status=200, arguments=None):
        offered = []
        responses = [response({'observations':[], 'claims_to_verify':[], 'limitations':[]}),
                     response(name='inspect_application_url', arguments={'application_url':'https://example.com/apply'}),
                     response(name='collect_application_page', arguments=arguments),
                     response({'risk_level':'insufficient evidence', 'explanation':'Identity remains unverified.'})]
        def chat(**kwargs):
            names = []
            for tool in kwargs.get('tools', []):
                names.append(tool.__name__ if callable(tool) else tool['function']['name'])
            if 'tools' in kwargs:
                offered.append(names)
            return responses.pop(0)
        client = Mock()
        client.chat.side_effect = chat
        investigator = Mock(return_value={'http_status':http_status, 'redirect_lookup_success':True,
            'final_url':'https://example.com/form', 'redirects':[]})
        evidence = {'status':'collected', 'extraction_success':True, 'text':'Upload resume',
                    'final_url':'https://example.com/form', 'redirects':[]}
        with patch('assess_job.collect_application_page', return_value=evidence) as collector:
            report = assess_job({'description':'Engineer role.', 'company_website':'https://example.com/',
                                 'application_url':'https://example.com/apply'}, model=model,
                                client=client, investigator=investigator, include_trace=True)
        return report, collector, offered, client

    def test_both_models_get_same_dependency_and_evidence(self):
        for model in ('qwen3.5:9b', 'llama3.1:8b'):
            report, collector, offered, client = self.run_agent(model)
            self.assertNotIn('collect_application_page', offered[0])
            self.assertIn('collect_application_page', offered[1])
            collector.assert_called_once_with('https://example.com/form')
            self.assertTrue(all(call.kwargs['model'] == model for call in client.chat.call_args_list))
            self.assertTrue(any(source['type']=='application_page_content' for source in report['evidence_sources']))
            facts = report['investigation']['summary_review']['observed_facts']
            self.assertEqual(facts['application_page_observations'][0]['text'], 'Upload resume')

    def test_blocked_inspection_does_not_offer_collector(self):
        report, collector, offered, _ = self.run_agent('test-model', http_status=403)
        self.assertNotIn('collect_application_page', offered[1])
        collector.assert_not_called()
        self.assertIn('error', report['investigation']['tool_calls'][1]['evidence'])

    def test_arbitrary_url_argument_is_rejected(self):
        report, collector, _, _ = self.run_agent('test-model', arguments={'final_url':'https://other.example.com/'})
        collector.assert_not_called()
        self.assertIn('error', report['investigation']['tool_calls'][1]['evidence'])

    def test_collection_redirects_and_fallback_are_preserved(self):
        facts = observed_facts({'tool_calls':[
            {'tool':'inspect_application_url','evidence':{'redirect_lookup_success':True,'redirects':[]}},
            {'tool':'collect_application_page','evidence':{'extraction_success':True,'redirects':[{'from':'https://example.com/a','to':'https://example.com/b'}]}}
        ]})
        self.assertEqual(summary_issues('The page redirects to a form.', facts), [])
        self.assertIn('Application destination HTML text was collected', factual_summary(facts))
