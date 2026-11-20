import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from job_evidence import posting_identifiers, identifier_match, page_details
from check_employer_association import check_employer_association


class IdentifierTests(unittest.TestCase):
    def test_airbnb_board_and_employer_ids_stay_separate(self):
        result = posting_identifiers({'job_id': '4492005458', 'source_website': 'LinkedIn',
            'application_url': 'https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fcareers.airbnb.com%2Fpositions%2F8360478'})
        self.assertEqual(result['source_listing_id'], '4492005458')
        self.assertEqual(result['employer'], [{'value':'8360478', 'source':'application_url'}])

    def test_explicit_job_code_and_unknown_legacy_id(self):
        result = posting_identifiers({'job_id':'A140181', 'description':'Job Code :\nA140181'})
        self.assertEqual(result['legacy_id_scope'], 'employer')
        self.assertEqual(result['employer'][0]['value'], 'A140181')
        self.assertEqual(posting_identifiers({'job_id':'12345'})['employer'], [])

    def test_board_paths_and_tracking_numbers_are_not_employer_ids(self):
        for url in ('https://www.linkedin.com/jobs/view/4492005458',
                    'https://www.indeed.com/viewjob?jk=abc123',
                    'https://www.ziprecruiter.com/jobs/12345',
                    'https://jobs.buildsubmarines.com/jobs/632023015-some-job'):
            self.assertEqual(posting_identifiers({'application_url':url})['employer'], [])
        self.assertEqual(posting_identifiers({'description':'Salary 123456. Apply now.'})['employer'], [])

    def test_exact_structured_id_match_and_neutral_missing(self):
        expected = [{'value':'R0123', 'source':'explicit_posting_label'}]
        self.assertEqual(identifier_match(expected, '', [{'identifier':{'value':'R0123'}}])['status'], 'matched')
        for value in ('R01234', 'R123', ''):
            result = identifier_match(expected, '', [{'identifier':value}])
            self.assertEqual(result['status'], 'not_established')
            self.assertFalse(result['affects_risk_when_missing'])

    def test_id_alone_does_not_override_wrong_title(self):
        result = page_details({'extraction_success':True, 'final_url':'https://careers.example.com/job/R0123',
            'text':'A readable page about an unrelated opening.', 'job_postings':[
                {'identifier':'R0123','title':'Accountant','description':'Work on accounts. '*20}]},
            {'job_title':'Engineer', 'description':'Job ID: R0123'})
        self.assertFalse(result['job_match'])

    def test_id_from_other_job_on_same_page_does_not_support_match(self):
        result = page_details({'extraction_success':True, 'final_url':'https://example.com/careers',
            'text':'Multiple openings', 'job_postings':[
                {'identifier':'R0123','title':'Accountant','description':'Accounts '*20},
                {'identifier':'R9999','title':'Engineer','description':'Build services '*20}]},
            {'job_title':'Engineer','description':'Job ID: R0123'})
        self.assertTrue(result['job_match'])
        self.assertEqual(result['identifier_check']['status'], 'not_established')

    def test_company_match_records_id_and_search_uses_id_within_budget(self):
        queries = []
        def search(query):
            queries.append(query)
            return [{'url':'https://example.com/jobs/R0123'}]
        def handler(request):
            html = '<h1>Engineer</h1><p>San Francisco</p>' if request.url.path != '/' else '<h1>Company</h1>'
            return httpx.Response(200, text=html, headers={'content-type':'text/html'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch('check_employer_association.validate_public_url'):
            result = check_employer_association('https://example.com/', 'Engineer', 'San Francisco',
                client=client, searcher=search, respect_robots=False,
                employer_job_ids=[{'value':'R0123','source':'explicit_posting_label'}])
        self.assertEqual(result['status'], 'matching_posting_found')
        self.assertEqual(result['matches'][0]['identifier_check']['status'], 'matched')
        self.assertIn('"R0123"', queries[0])
        self.assertLessEqual(len(queries), 2)


if __name__ == '__main__':
    unittest.main()
