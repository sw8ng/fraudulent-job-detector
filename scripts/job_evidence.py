"""Extract narrowly supported job facts without assigning fraud or identity labels."""

import json
import re
from html import unescape
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

from bs4 import BeautifulSoup


def url_job_id(url):
    """Read a job-path ID, keeping job-board identifiers out of employer evidence."""
    parsed = urlsplit(url or '')
    host = (parsed.hostname or '').lower()
    if any(host == board or host.endswith('.' + board)
           for board in ('linkedin.com', 'indeed.com', 'ziprecruiter.com', 'buildsubmarines.com')):
        return None
    if host == 'jobs.ashbyhq.com':
        parts = parsed.path.strip('/').split('/')
        if len(parts) == 2 and re.fullmatch(r'[a-fA-F0-9-]{36}', parts[1]):
            return parts[1]
    match = re.search(r'/(?:jobs?|positions?|listing)/(?:[^/]+/)*([A-Za-z0-9-]+?)/?$', parsed.path)
    if match and re.fullmatch(r'(?:[A-Za-z]{0,8}\d{3,}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})', match[1]):
        return match[1]
    return None


def posting_identifiers(posting):
    """Separate board IDs from employer IDs supported by links or explicit labels."""
    employer = []
    def add(value, source):
        if value and not any(item['value'].casefold() == value.casefold() for item in employer):
            employer.append({'value': value, 'source': source})
    url = posting.get('application_url') or ''
    # Decode only LinkedIn's explicit outbound URL field, without visiting it.
    parsed = urlsplit(url)
    if (parsed.hostname or '').lower() in ('www.linkedin.com', 'linkedin.com') and parsed.path == '/safety/go/':
        url = parse_qs(parsed.query).get('url', [''])[0]
    add(url_job_id(url), 'application_url')
    text = '\n'.join(str(posting.get(key) or '') for key in
                     ('description', 'responsibilities', 'qualifications', 'raw_posting_text'))
    for match in re.finditer(r'\b(?:requisition\s*(?:id|number|code|#)|job\s*(?:id|number|code|#))\s*[:#-]?\s*([A-Za-z0-9-]{3,80})\b', text, re.I):
        if any(char.isdigit() for char in match[1]):
            add(match[1], 'explicit_posting_label')
    supplied = posting.get('job_id')
    # An unqualified legacy job_id stays unclassified unless corroborated above.
    return {'employer': employer, 'legacy_job_id': supplied,
            'source_listing_id': supplied if str(posting.get('source_website') or '').casefold() == 'linkedin' else None,
            'source_listing_website': posting.get('source_website'),
            'legacy_id_scope': 'employer' if supplied and any(item['value'].casefold() == str(supplied).casefold() for item in employer) else 'unconfirmed'}


def identifier_match(expected, url, jobs, text=''):
    """Exact ID equality only; missing IDs are an optional, neutral result."""
    observed = []
    value = url_job_id(url)
    if value:
        observed.append(value)
    for job in jobs:
        identifier = job.get('identifier')
        if isinstance(identifier, dict):
            identifier = identifier.get('value')
        if isinstance(identifier, (str, int)):
            observed.append(str(identifier))
    # Explicit labels only: arbitrary numbers in descriptions are not identifiers.
    observed.extend(item['value'] for item in posting_identifiers({'description': text})['employer'])
    matched = [item for item in expected if item['value'].casefold() in {v.casefold() for v in observed}]
    return {'status': 'matched' if matched else 'not_established', 'matched': matched,
            'observed_ids': observed, 'affects_risk_when_missing': False}


def normalized(text):
    return ' '.join(re.findall(r'\w+', str(text or '').casefold()))


def location_matches(expected, observed):
    """Normalize common location formatting; never match on state alone."""
    if not expected:
        return True
    expected = re.sub(r'\([^)]*\)', '', expected)
    city = normalized(expected.split(',')[0])
    return bool(city and (' ' + city + ' ') in (' ' + normalized(observed) + ' '))


def title_matches(expected, observed):
    left, right = normalized(expected), normalized(observed)
    return bool(left and right and (' ' + left + ' ') in (' ' + right + ' '))


def structured_jobs(soup):
    """Read JobPosting JSON-LD, including arrays/graphs; never run page scripts."""
    jobs = []
    pending = []
    for tag in soup.find_all('script', type='application/ld+json')[:20]:
        try:
            pending.append(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError):
            continue
    inspected = 0
    while pending and inspected < 500:
        item = pending.pop()
        inspected += 1
        if isinstance(item, list):
            pending.extend(item[:100])
        elif isinstance(item, dict):
            kind = item.get('@type', [])
            if kind == 'JobPosting' or isinstance(kind, list) and 'JobPosting' in kind:
                job = {key: item[key] for key in ('title', 'description', 'identifier',
                    'hiringOrganization', 'jobLocation', 'jobLocationType', 'validThrough',
                    'datePosted', 'url') if key in item}
                jobs.append(job)
            else:
                pending.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return jobs[:20]


def job_text(job):
    description = BeautifulSoup(unescape(unescape(str(job.get('description') or ''))), 'html.parser').get_text(' ', strip=True)
    location = job.get('jobLocation') or job.get('location') or ''
    return '\n'.join([str(job.get('title') or ''), str(location), description]).strip()


def page_details(page, posting=None):
    """Distinguish closure, job text, shells, and unknown availability."""
    posting = posting or {}
    text = page.get('text') or ''
    result = {'content_kind': 'unavailable', 'availability': 'unknown',
              'availability_quote': '', 'job_match': False, 'title_match': False,
              'location_match': False}
    result['identifier_check'] = {'status': 'not_established', 'matched': [],
                                 'observed_ids': [], 'affects_risk_when_missing': False}
    if not page.get('extraction_success') or not text.strip():
        return result
    result['identifier_check'] = identifier_match(posting_identifiers(posting)['employer'],
        page.get('final_url'), page.get('job_postings') or [], text)
    result['content_kind'] = 'partial_text'
    # Bound closure notices to clear page notices, not quoted scam warnings.
    pattern = r'(?:this (?:job|posting|position) is no longer available|this (?:job|posting|position) (?:has expired|is closed)|applications (?:for this (?:role|position) )?are closed)'
    notice = re.search(pattern, text, re.I)
    if notice:
        result.update(content_kind='closed_notice', availability='closed', availability_quote=notice.group())
        return result
    if len(text) < 1500 and re.search(r'enable javascript|javascript (?:is )?required|sign in to continue|log in to continue', text, re.I):
        result['content_kind'] = 'javascript_shell' if 'javascript' in text.casefold() else 'login_shell'
        return result
    candidates = page.get('job_postings') or []
    for item in candidates:
        title_ok = title_matches(posting.get('job_title'), item.get('title'))
        place_ok = location_matches(posting.get('location'), job_text(item))
        if title_ok and place_ok and len(str(item.get('description') or '')) >= 80:
            result.update(content_kind='job_description', job_match=True, title_match=True, location_match=True)
            result['identifier_check'] = identifier_match(posting_identifiers(posting)['employer'],
                page.get('final_url'), [item])
            # A published endpoint observation establishes listing presence, not an open application.
            expiry = item.get('validThrough')
            if expiry:
                try:
                    date = datetime.fromisoformat(str(expiry).replace('Z', '+00:00'))
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    checked = page.get('checked_at')
                    observed = datetime.fromisoformat(checked.replace('Z', '+00:00')) if checked else datetime.now(timezone.utc)
                    if date < observed:
                        result.update(availability='expired', availability_quote='validThrough: ' + str(expiry))
                except (ValueError, TypeError):
                    pass
            return result
    title = posting.get('job_title')
    result['title_match'] = title_matches(title, page.get('page_title') or text[:400])
    result['location_match'] = location_matches(posting.get('location'), text)
    has_sections = bool(re.search(r'\bresponsibilities\b|\bqualifications\b|\bwhat you.ll do\b|\bwho we are\b|\brole overview\b|\babout (?:the|this) (?:job|role)\b', text, re.I))
    # A title plus template/configuration text is insufficient for a job match.
    if result['title_match'] and result['location_match'] and has_sections and len(text) >= 300:
        result.update(content_kind='job_description', job_match=True)
    return result
