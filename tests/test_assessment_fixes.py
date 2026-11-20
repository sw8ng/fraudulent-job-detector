import json
import sys
import unittest
import copy
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class EvidenceFixTests(unittest.TestCase):
    def test_summary_cannot_authenticate_company_from_recorded_checks(self):
        from summary_grounding import observed_facts, summary_issues
        facts = observed_facts({'tool_calls': []})
        self.assertTrue(summary_issues('The job posting is from a legitimate company.', facts))
        self.assertFalse(summary_issues('The evidence does not establish a legitimate company.', facts))

    def test_generation_schema_allows_only_recorded_finding_choices(self):
        from assess_job import reassess_saved
        from saved_results import posting_fingerprint
        from jsonschema import validate, ValidationError
        job = {'job_title': 'Engineer'}
        investigation = {'model': 'test', 'input_fingerprint': posting_fingerprint(job),
            'tool_calls': [{'tool': 'collect_application_page', 'evidence': {
                'extraction_success': True, 'text': 'This posting is no longer available.'}}]}
        valid = {'risk_level': 'low', 'explanation': 'The posting is no longer available.', 'findings': [
            {'id': 'posting_closed', 'quote': 'This posting is no longer available', 'evidence_ids': ['application-page-1']}]}
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason='stop', message=SimpleNamespace(content=json.dumps(valid)))
        reassess_saved(job, investigation, model='test', client=client)
        schema = client.chat.call_args_list[0].kwargs['format']
        validate(valid, schema)
        with self.assertRaises(ValidationError):
            validate(dict(valid, findings=[{'id': 'job_match_found', 'quote': '', 'evidence_ids': ['invented']}]), schema)

    def test_closed_notice_survives_report_and_fallback(self):
        from assessment_report import build_report
        from summary_grounding import factual_summary, observed_facts
        investigation = {'tool_calls': [{'tool': 'collect_application_page', 'evidence': {
            'extraction_success': True, 'final_url': 'https://example.com/jobs/1',
            'text': "We're sorry…this posting is no longer available."}}]}
        report = build_report({'job_title': 'Engineer'},
                              {'risk_level': 'insufficient evidence', 'explanation': ''}, investigation)
        self.assertEqual(report['conclusion']['availability']['status'], 'closed')
        self.assertIn('no longer available', factual_summary(observed_facts(investigation)))

    def test_shell_is_readable_but_not_job_content(self):
        from job_evidence import page_details
        result = page_details({'extraction_success': True, 'text':
            'Software Engineer @ Employer\nYou need to enable JavaScript to run this app.'})
        self.assertEqual(result['content_kind'], 'javascript_shell')
        self.assertEqual(result['availability'], 'unknown')

    def test_title_alone_does_not_establish_open_or_match(self):
        from job_evidence import page_details
        result = page_details({'extraction_success': True, 'page_title': 'Software Engineer',
                               'text': 'Software Engineer'}, {'job_title': 'Software Engineer'})
        self.assertFalse(result['job_match'])
        self.assertEqual(result['availability'], 'unknown')

    def test_job_jsonld_is_retained_without_executing_scripts(self):
        from collect_application_page import collect_application_page
        data = {'@type': 'JobPosting', 'title': 'Software Engineer',
                'description': '<p>Build services with Python.</p>',
                'jobLocation': {'address': {'addressLocality': 'San Francisco', 'addressRegion': 'California'}}}
        html = '<script type="application/ld+json">' + json.dumps(data) + '</script><script>bad()</script>'
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, text=html, headers={'content-type': 'text/html'}))) as client, \
                patch('collect_application_page.validate_public_url'):
            result = collect_application_page('https://example.com/jobs/1', client=client, respect_robots=False)
        self.assertIn('Build services', result['text'])
        self.assertEqual(result['job_postings'][0]['title'], 'Software Engineer')
        self.assertNotIn('bad()', result['text'])

    def test_location_variants_match_but_different_city_does_not(self):
        from job_evidence import location_matches
        self.assertTrue(location_matches('San Francisco, CA (Hybrid)', 'San Francisco, California, United States'))
        self.assertFalse(location_matches('San Francisco, CA', 'San Mateo, California'))

    def test_targeted_search_reads_same_company_job_not_snippet(self):
        from check_employer_association import check_employer_association
        def handler(request):
            html = ('<h1>Software Engineer</h1><p>San Francisco, California</p>'
                    if request.url.path == '/openings/123' else '<h1>Company</h1>')
            return httpx.Response(200, text=html, headers={'content-type': 'text/html'})
        searches = []
        def search(query):
            searches.append(query)
            return [{'url': 'https://example.com/openings/123', 'title': 'Software Engineer'},
                    {'url': 'https://imposter.org/jobs/123', 'title': 'Software Engineer'}]
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, \
                patch('check_employer_association.validate_public_url'):
            result = check_employer_association('https://example.com/', 'Software Engineer',
                'San Francisco, CA', client=client, searcher=search)
        self.assertEqual(result['status'], 'matching_posting_found')
        self.assertIn('site:example.com', searches[0])
        self.assertFalse(any('imposter' in p['url'] for p in result['pages_checked']))
        self.assertTrue(result['searches'])

    def test_exact_ashby_job_is_recovered_from_public_api(self):
        from collect_application_page import collect_application_page
        requests = []
        def handler(request):
            requests.append(str(request.url))
            if request.url.host == 'api.ashbyhq.com':
                return httpx.Response(200, json={'jobs': [
                    {'title': 'Other role', 'jobUrl': 'https://jobs.ashbyhq.com/acme/other', 'descriptionPlain': 'Wrong'},
                    {'title': 'Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/acme/exact',
                     'descriptionPlain': 'Build Python services.', 'location': 'San Francisco, CA'}]})
            return httpx.Response(200, text='You need to enable JavaScript to run this app.', headers={'content-type': 'text/html'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch('collect_application_page.validate_public_url'):
            result = collect_application_page('https://jobs.ashbyhq.com/acme/exact', client=client, respect_robots=False)
        self.assertIn('Build Python services.', result['text'])
        self.assertNotIn('Wrong', result['text'])
        self.assertEqual(result['structured_source']['provider'], 'ashby')
        self.assertEqual(len(requests), 2)

    def test_search_snippet_cannot_create_match_when_page_is_blocked(self):
        from check_employer_association import check_employer_association
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client, \
                patch('check_employer_association.validate_public_url'):
            result = check_employer_association('https://example.com/', 'Engineer', 'San Francisco', client=client, respect_robots=False,
                searcher=lambda _: [{'url': 'https://example.com/jobs/1', 'description': 'Engineer San Francisco'}])
        self.assertNotEqual(result['status'], 'matching_posting_found')

    def test_targeted_careers_index_follows_exact_role_link(self):
        from check_employer_association import check_employer_association
        def handler(request):
            if request.url.path == '/index':
                text = '<h1>Careers</h1><a href="https://jobs.ashbyhq.com/acme/123">Software Engineer</a>'
            elif request.url.host == 'jobs.ashbyhq.com':
                text = '<h1>Software Engineer</h1><p>San Francisco, California</p>'
            else:
                text = '<h1>Company</h1>'
            return httpx.Response(200, text=text, headers={'content-type': 'text/html'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, \
                patch('check_employer_association.validate_public_url'):
            result = check_employer_association('https://example.com/', 'Software Engineer',
                'San Francisco, CA', client=client, respect_robots=False,
                searcher=lambda _: [{'url': 'https://example.com/index'}])
        self.assertEqual(result['matches'][0]['url'], 'https://jobs.ashbyhq.com/acme/123')
        self.assertFalse(result['verified'])

    def test_greenhouse_exact_job_public_data(self):
        from collect_application_page import collect_application_page
        def handler(request):
            if request.url.host == 'boards-api.greenhouse.io':
                return httpx.Response(200, json={'id': 123, 'title': 'Engineer',
                    'content': '&lt;p&gt;Build Python services.&lt;/p&gt;', 'location': {'name': 'San Francisco, CA'}})
            return httpx.Response(200, text='Enable JavaScript', headers={'content-type': 'text/html'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch('collect_application_page.validate_public_url'):
            result = collect_application_page('https://boards.greenhouse.io/acme/jobs/123', client=client, respect_robots=False)
        self.assertEqual(result['structured_source']['provider'], 'greenhouse')
        self.assertIn('Build Python services.', result['text'])
        self.assertNotIn('&lt;p&gt;', result['text'])

    def test_saved_evidence_regeneration_corrects_bad_findings_without_tools(self):
        from assess_job import reassess_saved
        from saved_results import posting_fingerprint
        job = {'job_title': 'Engineer'}
        investigation = {'model': 'test', 'input_fingerprint': posting_fingerprint(job),
            'tool_calls': [{'tool': 'collect_application_page', 'seconds': 10, 'evidence': {
                'extraction_success': True, 'text': 'This posting is no longer available.',
                'final_url': 'https://example.com/jobs/1'}}]}
        original = copy.deepcopy(investigation)
        bad = {'risk_level': 'low', 'explanation': 'The job is open.', 'findings': [
            {'id': 'job_match_found', 'quote': '', 'evidence_ids': ['invented']}]}
        good = {'risk_level': 'low', 'explanation': 'The posting is no longer available; closure does not establish fraud.',
                'findings': [{'id': 'posting_closed', 'quote': 'This posting is no longer available.',
                              'evidence_ids': ['application-page-1']}]}
        client = Mock()
        client.chat.side_effect = [SimpleNamespace(done_reason='stop', message=SimpleNamespace(content=json.dumps(x))) for x in (bad, good)]
        with patch('assess_job.collect_application_page', side_effect=AssertionError('No new collection')), \
             patch('assess_job.find_company_website', side_effect=AssertionError('No new search')):
            report = reassess_saved(job, investigation, model='test', client=client)
        self.assertEqual(investigation, original)
        self.assertEqual(report['conclusion']['availability']['status'], 'closed')
        self.assertEqual(report['assessment']['findings'], good['findings'])
        self.assertEqual(report['investigation']['summary_review']['original_assessment'], bad)
        self.assertEqual(len(report['investigation']['performance']['model_calls']), 2)

    def test_persistent_bad_findings_fall_back_without_fabricated_retention(self):
        from assess_job import reassess_saved
        from saved_results import posting_fingerprint
        job = {'job_title': 'Engineer'}
        investigation = {'model': 'test', 'input_fingerprint': posting_fingerprint(job), 'tool_calls': []}
        bad = {'risk_level': 'low', 'explanation': 'Nothing found.', 'findings': [
            {'id': 'posting_closed', 'quote': '', 'evidence_ids': ['posting-1']}]}
        client = Mock()
        client.chat.return_value = SimpleNamespace(done_reason='stop', message=SimpleNamespace(content=json.dumps(bad)))
        report = reassess_saved(job, investigation, model='test', client=client)
        self.assertNotIn('findings', report['assessment'])
        self.assertEqual(report['findings_status'], 'unavailable_after_factual_fallback')
        self.assertEqual(report['assessment']['risk_level'], 'insufficient evidence')

    def test_replay_mismatch_stops_before_model_call(self):
        from assess_job import reassess_saved
        client = Mock()
        with self.assertRaises(ValueError):
            reassess_saved({'job_title': 'Engineer'}, {'input_fingerprint': 'wrong'}, model='test', client=client)
        client.chat.assert_not_called()

    def test_replay_runner_preserves_old_timeout_without_retry_or_overwrite(self):
        from compare_models import reassess_comparison
        from saved_results import posting_fingerprint
        from assessment_report import build_report
        job = {'job_title': 'Engineer'}
        investigation = {'model': 'test', 'input_fingerprint': posting_fingerprint(job),
                         'tool_calls': [], 'content_analysis': {}, 'summary_review': {'status': 'passed'}}
        report = build_report(job, {'risk_level': 'low', 'explanation': 'Original.'}, investigation)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'report-001.json').write_text(json.dumps(report))
            partial = dict(report, execution_status='timed_out')
            (root/'report-002.json').write_text(json.dumps(partial))
            rows = [{'case': 'input.json', 'source': 'input', 'model': 'test', 'repetition': 1,
                     'status': 'completed', 'report': 'report-001.json'},
                    {'case': 'input.json', 'source': 'input', 'model': 'test', 'repetition': 2,
                     'status': 'timed_out', 'report': 'report-002.json'}]
            comparison = {'models': ['test'], 'cases': [{'name': 'input.json', 'kind': 'saved_posting', 'source': 'input', 'job': job}], 'runs': rows}
            (root/'comparison.json').write_text(json.dumps(comparison))
            before = (root/'report-001.json').read_bytes()
            with patch('compare_models.run_bounded_assessment', return_value=report) as worker:
                result = reassess_comparison(root/'comparison.json', root/'new', max_seconds=1, max_total_seconds=30)
            self.assertEqual(worker.call_count, 1)
            self.assertIn('saved_investigation', worker.call_args.kwargs)
            self.assertEqual(result['runs'][1]['status'], 'timed_out')
            self.assertTrue(result['runs'][1]['preserved_incomplete'])
            self.assertEqual((root/'report-001.json').read_bytes(), before)
            self.assertTrue((root/'new/preserved-002.json').exists())


if __name__ == '__main__':
    unittest.main()
