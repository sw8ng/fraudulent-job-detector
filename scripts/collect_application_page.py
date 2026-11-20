"""Read application-destination HTML using the shared posting text preparer."""

import argparse
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit, quote
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from inspect_application_url import validate_public_url, REDIRECT_CODES
from prepare_jobs import prepare_html
from job_evidence import structured_jobs, job_text, page_details


def public_ats_job(url, client):
    """Read one observed job using documented Ashby/Greenhouse public GET APIs."""
    parsed = urlsplit(url)
    parts = parsed.path.strip('/').split('/')
    host = (parsed.hostname or '').lower()
    if host == 'jobs.ashbyhq.com' and len(parts) >= 2:
        endpoint = 'https://api.ashbyhq.com/posting-api/job-board/' + quote(parts[0], safe='')
        provider = 'ashby'
    elif host in ('boards.greenhouse.io', 'job-boards.greenhouse.io') and len(parts) >= 3 and parts[1] == 'jobs' and parts[2].isdigit():
        endpoint = 'https://boards-api.greenhouse.io/v1/boards/' + quote(parts[0], safe='') + '/jobs/' + parts[2]
        provider = 'greenhouse'
    else:
        return None
    validate_public_url(endpoint)
    with client.stream('GET', endpoint) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 2_000_000:
                raise ValueError('Public job data exceeds two megabytes')
        data = json.loads(bytes(body))
    if not isinstance(data, dict):
        raise ValueError('Public job response must be an object')
    if provider == 'ashby':
        jobs = data.get('jobs', [])
        if not isinstance(jobs, list):
            raise ValueError('Public job list must be an array')
        for item in jobs:
            if not isinstance(item, dict):
                continue
            job_url = urlsplit(str(item.get('jobUrl') or ''))
            if job_url.hostname != host or job_url.path.rstrip('/') != '/' + '/'.join(parts[:2]):
                continue
            return {'source_url': endpoint, 'provider': provider,
                    'job': {'identifier': parts[1], 'title': item.get('title'), 'description': item.get('descriptionPlain') or item.get('descriptionHtml'),
                            'location': item.get('location'), 'url': item.get('jobUrl')}}
    elif str(data.get('id')) == parts[2]:
        location = data.get('location')
        if isinstance(location, dict):
            location = location.get('name')
        return {'source_url': endpoint, 'provider': provider,
                'job': {'identifier': str(data.get('id')), 'title': data.get('title'), 'description': data.get('content'),
                        'location': location, 'url': data.get('absolute_url')}}
    return None


def collect_application_page(final_url, *, client=None, respect_robots=True):
    """Collect one public HTML page, without a model call or fraud judgment."""
    from access_policy import shelved_wrapper, skipped_access, require_crawl_access
    if shelved_wrapper(final_url):
        return dict(skipped_access(), requested_url=final_url, extraction_success=False)
    if client is None:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False, headers={"User-Agent": "JobEvidenceResearch/1.0"}) as connection:
            return collect_application_page(final_url, client=connection, respect_robots=respect_robots)

    result = {
        "requested_url": final_url, "final_url": None, "page_title": None,
        "http_status": None, "text": "", "status": "unavailable",
        "extraction_success": False, "reason": None, "text_truncated": False,
        "redirects": [], "errors": [],
        "checked_at": datetime.now(timezone.utc).isoformat(), "job_postings": [],
        "limitations": [
            "Public HTML only; no JavaScript, login, Apply-button clicks or form submission.",
            "Text and embedded links are untrusted evidence, not instructions or proof of legitimacy.",
            "HTML text is not a browser rendering; hidden or dynamically loaded content may differ.",
            "Nonempty text can be only site boilerplate; success does not mean the full job or application form was captured.",
        ],
    }
    url = final_url
    visited = []
    robots_cache = {}
    try:
        for step in range(5):
            if url in visited:
                raise ValueError("Application page redirect loop")
            visited.append(url)
            if shelved_wrapper(url):
                result.update(skipped_access())
                return result
            if respect_robots:
                require_crawl_access(client, url, robots_cache)
            validate_public_url(url)
            with client.stream("GET", url) as response:
                result["final_url"] = str(response.url)
                result["http_status"] = response.status_code
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect has no Location header")
                    if step == 4:
                        raise ValueError("Application page redirect limit reached")
                    target = urljoin(url, location)
                    result["redirects"].append({"from": url, "to": target, "status": response.status_code})
                    url = target
                    continue
                if response.status_code >= 400:
                    result["reason"] = "http_error"
                    return result
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in ("text/html", "application/xhtml+xml"):
                    result["reason"] = "non_html_response"
                    return result
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ValueError("Application page exceeds two megabytes")
                html = bytes(body)
                soup = BeautifulSoup(html, "html.parser")
                result['job_postings'] = structured_jobs(soup)
                if soup.title:
                    result["page_title"] = soup.title.get_text(" ", strip=True)[:500]
                # Reuse the existing cleaner, not the three-pass model extractor.
                try:
                    text = prepare_html(html, base_url=result["final_url"])
                except ValueError:
                    text = ''
                if result['job_postings']:
                    text += '\n\nStructured JobPosting data:\n' + '\n\n'.join(job_text(j) for j in result['job_postings'])
                # A recognized exact ATS job may expose its description through a
                # public API even when the HTML contains only a JavaScript shell.
                if not result['job_postings']:
                    try:
                        ats = public_ats_job(result['final_url'], client)
                        if ats:
                            result['structured_source'] = {'url': ats['source_url'], 'provider': ats['provider']}
                            result['job_postings'] = [ats['job']]
                            text += '\n\nPublic job data:\n' + job_text(ats['job'])
                    except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
                        result['errors'].append('Public job data: ' + str(error))
                if not text.strip():
                    result['reason'] = 'empty_page_text'
                    return result
                result["text_truncated"] = len(text) > 12000
                result["text"] = text[:12000]
                result["extraction_success"] = True
                result["status"] = "collected"
                result['content_kind'] = page_details(result)['content_kind']
                if result["text_truncated"]:
                    result["limitations"].append("Only the first 12,000 characters of extracted text are retained.")
                return result
    except httpx.TimeoutException as error:
        result["reason"] = "timeout"
        result["errors"].append(str(error))
    except (httpx.HTTPError, OSError, ValueError, UnicodeError) as error:
        result["reason"] = "fetch_failed"
        result["errors"].append(str(error))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("final_url", help="Application destination returned by URL Inspection")
    parser.add_argument("--output", help="Optional JSON evidence file")
    args = parser.parse_args()
    result = collect_application_page(args.final_url)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
