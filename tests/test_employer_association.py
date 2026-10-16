import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_employer_association import check_employer_association


class AssociationTests(unittest.TestCase):
    def test_exact_job_link_prioritized_after_large_index(self):
        def handler(request):
            html = ('<h1>Software Engineer</h1><p>San Mateo, CA</p>' if request.url.path == '/target'
                    else ''.join(f'<a href="/jobs/{i}">Careers</a>' for i in range(20)) +
                    '<a href="/target">Software Engineer</a>')
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        result = self.check(handler)
        self.assertEqual(result["status"], "matching_posting_found")
        self.assertEqual(len(result["pages_checked"]), 2)

    def check(self, handler):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch("check_employer_association.validate_public_url"):
            return check_employer_association("https://example.com/", "Software Engineer", "San Mateo, CA", client=client)

    def test_linked_hiring_platform_match_is_not_identity_verification(self):
        def handler(request):
            html = ('<footer><a href="https://ats.example.org/jobs/1">Software Engineer</a></footer>' if request.url.host == "example.com"
                    else '<h1>Software Engineer</h1><p>San Mateo, CA</p>')
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        result = self.check(handler)
        self.assertEqual(result["status"], "matching_posting_found")
        self.assertEqual(len(result["pages_checked"]), 2)
        self.assertFalse(result["verified"])

    def test_title_only_elsewhere_is_not_a_matching_posting(self):
        result = self.check(lambda _: httpx.Response(200, text='<h1>Our company</h1><p>Software Engineer San Mateo, CA</p>', headers={"content-type": "text/html"}))
        self.assertEqual(result["status"], "not_found_in_checked_pages")

    def test_blocked_page_is_could_not_check(self):
        result = self.check(lambda _: httpx.Response(403))
        self.assertEqual(result["status"], "could_not_check")

    def test_page_limit_and_location_mismatch(self):
        counter = []
        def handler(request):
            counter.append(str(request.url))
            html = '<h1>Software Engineer</h1><p>Austin, TX</p>' + ''.join(f'<a href="/jobs/{i}">Careers</a>' for i in range(10))
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        result = self.check(handler)
        self.assertEqual(result["status"], "not_found_in_checked_pages")
        self.assertEqual(len(counter), 4)


if __name__ == "__main__":
    unittest.main()
