"""Bounded public access checks; unavailable sources are neutral evidence."""

from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser


def shelved_wrapper(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    path = parsed.path.rstrip('/').lower()
    return ((host == 'indeed.com' or host.endswith('.indeed.com'))
            and path in ('/onboarding', '/auth', '/thirdpartysignin')) or (
        (host == 'ziprecruiter.com' or host.endswith('.ziprecruiter.com'))
        and path == '/job-redirect')


def skipped_access():
    return {'status': 'skipped', 'reason': 'unsupported_apply_wrapper',
            'access_status': 'unavailable', 'assessment_impact': 'none'}


def validate_public_url(url):
    from inspect_application_url import validate_public_url as validate
    validate(url)


def require_crawl_access(client, url, cache):
    """Read robots once per origin, validating every redirect before requesting it."""
    parsed = urlsplit(url)
    origin = parsed.scheme + '://' + parsed.netloc
    if origin not in cache:
        robots_url = origin + '/robots.txt'
        for _ in range(4):
            validate_public_url(robots_url)
            with client.stream('GET', robots_url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if not response.headers.get('location'):
                        raise ValueError('Robots redirect has no destination')
                    robots_url = urljoin(robots_url, response.headers['location'])
                    continue
                if response.status_code in (404, 410):
                    cache[origin] = None
                    break
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ValueError('Robots policy exceeds size limit')
                policy = RobotFileParser()
                policy.parse(body.decode('utf-8', errors='replace').splitlines())
                cache[origin] = policy
                break
        else:
            raise ValueError('Robots redirect limit reached')
    policy = cache[origin]
    if policy is not None and not policy.can_fetch('JobEvidenceResearch', url):
        raise ValueError('Crawler access prohibited by robots.txt')


def neutral_access(evidence):
    """Identify failures without treating a readable closure notice as a failure."""
    return (evidence.get('assessment_impact') == 'none'
            or evidence.get('access_status') == 'unavailable'
            or evidence.get('status') in ('skipped', 'unavailable', 'could_not_check')
            or (evidence.get('http_status') or 0) >= 400
            or evidence.get('extraction_success') is False
            or evidence.get('lookup_success') is False
            or evidence.get('redirect_lookup_success') is False)
