"""Look for a job on a candidate company's site and explicitly linked careers pages."""

import argparse
import json
import re
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode

import httpx
from ddgs.exceptions import DDGSException
from bs4 import BeautifulSoup

from inspect_application_url import validate_public_url, REDIRECT_CODES, domain_for
from access_policy import require_crawl_access
from job_evidence import location_matches, structured_jobs, job_text, title_matches, identifier_match


def page_key(url):
    """Ignore fragments and known tracking keys, retaining job IDs/pagination."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    if port and port not in (80, 443):
        host += f":{port}"
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("hslang", "gh_src")]
    return host + (parsed.path.rstrip("/") or "/") + "?" + urlencode(sorted(query))


def careers_link(url, label):
    parsed = urlsplit(url)
    return bool(re.search(r"\b(?:careers?|jobs?|open positions|opportunities|vacancies|join our team)\b", label, re.I)
                or re.search(r"/(?:careers?|jobs?|positions?|openings)(?:/|$)", parsed.path, re.I)
                or (parsed.hostname or "").lower().startswith(("careers.", "jobs.")))


def read_page(client, url, robots_cache=None):
    for _ in range(4):
        if robots_cache is not None:
            require_crawl_access(client, url, robots_cache)
        validate_public_url(url)
        with client.stream("GET", url) as response:
            if response.status_code in REDIRECT_CODES:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect has no Location header")
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", "").lower():
                raise ValueError("Page is not HTML")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 8_000_000:
                    raise ValueError("Page exceeds size limit")
            return url, BeautifulSoup(bytes(body), "html.parser"), response.status_code
    raise ValueError("Page redirect limit reached")


def normalized(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def check_employer_association(company_website, job_title, location=None, *, client=None, searcher=None, respect_robots=True, employer_job_ids=None):
    """A found match supports association with a candidate site, not identity verification."""
    employer_job_ids = employer_job_ids or []
    result = {"status": "could_not_check", "company_website": company_website,
              'checked_at': datetime.now(timezone.utc).isoformat(),
              "job_title": job_title, "location": location, "verified": False,
              "matches": [], "pages_checked": [], "discovered_links": [], "errors": [], "searches": [],
              "limitations": ["Company website is an unverified candidate or supplied claim.",
                              "Checks at most four linked HTML pages/eight crawl attempts, then two targeted queries and three fetched search candidates; no JavaScript or login. Absence is not fraud evidence."]}
    result['employer_job_ids'] = employer_job_ids
    if not company_website or not job_title:
        result["limitations"].append("A company website and job title are required")
        return result
    if client is None:
        from find_company_website import search_web
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False, headers={"User-Agent": "JobEvidenceResearch/1.0"}) as connection:
            return check_employer_association(company_website, job_title, location,
                client=connection, searcher=searcher or search_web, respect_robots=respect_robots, employer_job_ids=employer_job_ids)
    robots_cache = {} if respect_robots else None
    queue = deque([company_website])
    visited = set()
    final_pages = set()
    attempts = 0
    successful = 0
    title = normalized(job_title)
    place = normalized(location or "")
    while queue and len(final_pages) < 4 and attempts < 8:
        requested = queue.popleft()
        key = page_key(requested)
        if key in visited or key in final_pages:
            continue
        visited.add(key)
        attempts += 1
        try:
            final_url, soup, http_status = read_page(client, requested, robots_cache)
            final_key = page_key(final_url)
            if final_key in final_pages:
                continue
            final_pages.add(final_key)
            # Careers links commonly live in navigation/footer elements. Keep
            # them before removing boilerplate from text used for matching.
            links = [(link["href"], link.get_text(" ", strip=True)) for link in soup.find_all("a", href=True)]
            # Follow explicit careers roots before broad department links.
            links.sort(key=lambda pair: (not title_matches(job_title, pair[1]),
                urlsplit(urljoin(final_url, pair[0])).path.rstrip("/") not in ("/careers", "/jobs")))
            jobs = structured_jobs(soup)
            for tag in soup.find_all(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            headings = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
            title_match = next((h for h in headings if title and f" {title} " in f" {normalized(h)} "), None)
            location_match = bool(place and location_matches(location, text))
            for item in jobs:
                if title_matches(job_title, item.get('title')) and location_matches(location, job_text(item)):
                    title_match = item.get('title')
                    location_match = bool(place)
            matching_jobs = [item for item in jobs if title_matches(job_title, item.get('title'))
                             and location_matches(location, job_text(item))]
            id_check = identifier_match(employer_job_ids, final_url, matching_jobs, '' if jobs else text)
            result["pages_checked"].append({"requested_url": requested, "url": final_url,
                "http_status": http_status,
                "identifier_check": id_check, "heading_match": title_match, "location_match": location_match if place else None})
            successful += 1
            if title_match and (not place or location_match):
                result["matches"].append({"url": final_url, "heading_quote": title_match, "identifier_check": id_check,
                    "location_match": location_match if place else None,
                    "basis": "Job title appears in a page heading; supplied location also appears on page when available"})
                result["status"] = "matching_posting_found"
                break
            for href, label in links:
                target = urljoin(final_url, href).split("#")[0]
                if urlsplit(target).scheme not in ("http", "https"):
                    continue
                if page_key(target) in visited or page_key(target) in final_pages:
                    continue
                # External ATS pages are eligible only through links on inspected pages.
                id_link = identifier_match(employer_job_ids, target, [])['status'] == 'matched'
                if (id_link or careers_link(target, label)
                        or (title and title in normalized(label))):
                    result["discovered_links"].append({"from_url": final_url, "to_url": target, "label": label[:300]})
                    if target not in visited and target not in queue:
                        if id_link or title and title in normalized(label):
                            if len(queue) >= 12:
                                queue.pop()
                            queue.appendleft(target)
                        elif len(queue) < 12:
                            queue.append(target)
        except (httpx.HTTPError, OSError, ValueError) as error:
            failure = {"url": requested, "message": str(error)}
            if isinstance(error, httpx.HTTPStatusError):
                failure["url"] = str(error.response.url)
                failure["requested_url"] = requested
                failure["http_status"] = error.response.status_code
            result["errors"].append(failure)
            result.update(status="could_not_check", access_status="unavailable", assessment_impact="none")
            return result
    if not result['matches'] and searcher is not None:
        targeted_job_search(result, job_title, location, client, searcher, robots_cache, employer_job_ids)
    if not result["matches"] and successful and result.get("access_status") != "unavailable":
        result["status"] = "not_found_in_checked_pages"
    return result


def targeted_job_search(result, job_title, location, client, searcher, robots_cache, employer_job_ids):
    """Fetch search candidates only inside the company domain or a linked ATS tenant."""
    website = result['company_website']
    company_domain = domain_for(website)
    linked = [link['to_url'] for link in result['discovered_links']]
    scopes = [company_domain]
    for url in linked:
        parsed = urlsplit(url)
        scope = parsed.netloc + '/' + parsed.path.strip('/').split('/')[0]
        if domain_for(url) != company_domain and scope not in scopes:
            scopes.append(scope)
    def eligible(url):
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http'):
            return False
        if domain_for(url) == company_domain:
            return True
        # Keep a shared hiring platform scoped to the employer's linked board.
        for known in linked:
            origin = urlsplit(known)
            board = '/' + origin.path.strip('/').split('/')[0]
            if parsed.netloc == origin.netloc and board != '/' and (parsed.path == board or parsed.path.startswith(board + '/')):
                return True
        return False
    fetched = set()
    queries = ['site:' + scopes[0] + ' "' + job_title.replace('"', '') + '" ' + (location or ''),
               'site:' + scopes[min(1, len(scopes)-1)] + ' "' + job_title.replace('"', '') + '"']
    if employer_job_ids:
        queries.insert(0, 'site:' + scopes[0] + ' "' + employer_job_ids[0]['value'] + '" "' + job_title.replace('"', '') + '"')
    for query in list(dict.fromkeys(queries))[:2]:
        try:
            rows = searcher(query)[:5]
            result['searches'].append({'query': query, 'results': rows})
        except (DDGSException, OSError, ValueError, TypeError) as error:
            result['errors'].append({'query': query, 'message': 'Targeted search failed: ' + str(error)})
            continue
        pending = list(rows)
        for row in pending:
            url = row.get('url', '')
            if not eligible(url) or url in fetched or len(fetched) >= 3:
                continue
            fetched.add(url)
            try:
                final, soup, code = read_page(client, url, robots_cache)
                if not eligible(final):
                    result['errors'].append({'url': final, 'message': 'Search result redirected outside the company/linked-board scope'})
                    continue
                jobs = structured_jobs(soup)
                headings = [h.get_text(' ', strip=True) for h in soup.find_all('h1')]
                # A targeted result can be an employer careers index. Follow its
                # exact title links within the same three-fetch budget.
                for link in soup.find_all('a', href=True):
                    label = link.get_text(' ', strip=True)
                    target = urljoin(final, link['href']).split('#')[0]
                    if title_matches(job_title, label):
                        result['discovered_links'].append({'from_url': final, 'to_url': target, 'label': label[:300]})
                        if domain_for(final) == company_domain:
                            linked.append(target)
                        if eligible(target) and target not in fetched:
                            pending.append({'url': target, 'title': label, 'method': 'title_link_on_fetched_search_result'})
                for tag in soup.find_all(['script', 'style', 'nav', 'footer']):
                    tag.decompose()
                text = soup.get_text(' ', strip=True)
                heading = next((h for h in headings if title_matches(job_title, h)), None)
                place = location_matches(location, text)
                for item in jobs:
                    if title_matches(job_title, item.get('title')) and location_matches(location, job_text(item)):
                        heading, place = item['title'], True
                matching_jobs = [item for item in jobs if title_matches(job_title, item.get('title'))
                                 and location_matches(location, job_text(item))]
                id_check = identifier_match(employer_job_ids or [], final, matching_jobs, '' if jobs else text)
                result['pages_checked'].append({'requested_url': url, 'url': final, 'http_status': code,
                    'identifier_check': id_check, 'heading_match': heading, 'location_match': place, 'method': 'targeted_search_fetch'})
                if heading and place:
                    result['matches'].append({'url': final, 'heading_quote': heading, 'identifier_check': id_check, 'location_match': place,
                        'basis': 'Targeted search result fetched inside company/linked-board scope; title and location matched'})
                    result['status'] = 'matching_posting_found'
                    return
            except (httpx.HTTPError, OSError, ValueError) as error:
                result['errors'].append({'url': url, 'message': str(error)})
                result.update(status='could_not_check', access_status='unavailable', assessment_impact='none')
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company_website")
    parser.add_argument("job_title")
    parser.add_argument("--location")
    args = parser.parse_args()
    print(json.dumps(check_employer_association(args.company_website, args.job_title, args.location), indent=2))
