import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class AccessPolicyTests(unittest.TestCase):
    def test_collector_skips_wrapper_before_opening_client(self):
        from collect_application_page import collect_application_page
        with patch('collect_application_page.httpx.Client', side_effect=AssertionError('No network')):
            result = collect_application_page('https://www.ziprecruiter.com/job-redirect?id=1')
        self.assertEqual(result['assessment_impact'], 'none')
        self.assertFalse(result['extraction_success'])

    def test_redirect_into_wrapper_stops_before_requesting_it(self):
        from follow_redirects import follow_redirects
        requests = []
        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={'location':'https://secure.indeed.com/auth'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch('inspect_application_url.validate_public_url'):
            result = follow_redirects('https://example.com/apply', client=client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(result['status'], 'skipped')
        self.assertFalse(result['redirect_lookup_success'])

    def test_collector_robot_denial_does_not_fetch_page_or_api(self):
        from collect_application_page import collect_application_page
        requests = []
        def handler(request):
            requests.append(request.url.path)
            return httpx.Response(200, text='User-agent: *\nDisallow: /')
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch('access_policy.validate_public_url'):
            result = collect_application_page('https://jobs.ashbyhq.com/acme/1', client=client)
        self.assertEqual(requests, ['/robots.txt'])
        self.assertFalse(result['extraction_success'])
        self.assertEqual(result['status'], 'unavailable')

    def test_known_apply_wrappers_are_skipped_without_network(self):
        from inspect_application_url import inspect_application_url
        for url in ['https://www.indeed.com/onboarding?continue=x',
                    'https://secure.indeed.com/auth?continue=x',
                    'https://www.ziprecruiter.com/job-redirect?match_token=x']:
            with patch('inspect_application_url.httpx.Client', side_effect=AssertionError('No wrapper request')):
                result = inspect_application_url(url)
            self.assertEqual(result['status'], 'skipped')
            self.assertEqual(result['assessment_impact'], 'none')

    def test_direct_links_are_not_shelved_because_of_board_name_in_query(self):
        from access_policy import shelved_wrapper
        self.assertFalse(shelved_wrapper('https://careers.example.com/jobs/1?utm_source=ziprecruiter'))
        self.assertFalse(shelved_wrapper('https://www.indeed.com/viewjob?jk=123'))

    def test_crawler_respects_robots_and_does_not_search_after_denial(self):
        from check_employer_association import check_employer_association
        requests = []
        def handler(request):
            requests.append(str(request.url))
            self.assertEqual(request.url.path, '/robots.txt')
            return httpx.Response(200, text='User-agent: *\nDisallow: /\n')
        search = Mock(side_effect=AssertionError('No search after access exit'))
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, \
             patch('check_employer_association.validate_public_url'), patch('access_policy.validate_public_url'):
            result = check_employer_association('https://example.com/', 'Engineer', client=client, searcher=search)
        self.assertEqual(result['status'], 'could_not_check')
        self.assertEqual(result['access_status'], 'unavailable')
        self.assertEqual(result['matches'], [])
        self.assertEqual(len(requests), 1)

    def test_crawler_stops_at_blocked_careers_instead_of_calling_job_absent(self):
        from check_employer_association import check_employer_association
        requests = []
        def handler(request):
            requests.append(request.url.path)
            if request.url.path == '/robots.txt': return httpx.Response(404)
            if request.url.path == '/': return httpx.Response(200, text='<a href="/careers">Careers</a>', headers={'content-type':'text/html'})
            return httpx.Response(403)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, \
             patch('check_employer_association.validate_public_url'), patch('access_policy.validate_public_url'):
            result = check_employer_association('https://example.com/', 'Engineer', client=client,
                searcher=Mock(side_effect=AssertionError('No retries after block')))
        self.assertEqual(result['status'], 'could_not_check')
        self.assertEqual(result['access_status'], 'unavailable')
        self.assertEqual(requests, ['/robots.txt', '/', '/careers'])

    def test_blocked_sources_and_labelled_filenames_are_not_in_risk_packet(self):
        from assessment_report import build_report
        from assess_job import assessment_packet, grounded_assessment_schema
        job={'source_file':'L_Engineer.pdf', 'label':'legitimate', 'job_title':'Engineer', 'description':'Build services.'}
        inv={'tool_calls':[{'tool':'collect_application_page','evidence':{
            'http_status':403,'extraction_success':False,'reason':'http_error'}}],
            'employer_association':{'status':'could_not_check','errors':[{'http_status':403}]}}
        report=build_report(job,{'risk_level':'low','explanation':'No specific warning signs.'},inv)
        packet=assessment_packet(job,report)
        self.assertNotIn('403',json.dumps(packet))
        self.assertNotIn('L_Engineer',json.dumps(packet))
        self.assertEqual(packet['access_failures_affect_risk'],False)
        _,options=grounded_assessment_schema(report)
        self.assertFalse(any(o['id']=='application_page_unavailable' for o in options))
        self.assertEqual(report['conclusion']['availability']['status'],'unknown')


if __name__ == '__main__':
    unittest.main()
