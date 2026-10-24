"""Look for a job on a candidate company's site and explicitly linked careers pages."""

import argparse
import json
import re
from collections import deque
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode

import httpx
from bs4 import BeautifulSoup

from inspect_application_url import validate_public_url, REDIRECT_CODES


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


def read_page(client, url):
    for _ in range(4):
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


def check_employer_association(company_website, job_title, location=None, *, client=None):
    """A found match supports association with a candidate site, not identity verification."""
    result = {"status": "could_not_check", "company_website": company_website,
              "job_title": job_title, "location": location, "verified": False,
              "matches": [], "pages_checked": [], "errors": [],
              "limitations": ["Company website is an unverified candidate or supplied claim.",
                              "Checks at most four distinct HTML pages and eight candidate fetch attempts, without JavaScript or login; absence is not fraud evidence."]}
    if not company_website or not job_title:
        result["limitations"].append("A company website and job title are required")
        return result
    if client is None:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as connection:
            return check_employer_association(company_website, job_title, location, client=connection)
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
            final_url, soup, http_status = read_page(client, requested)
            final_key = page_key(final_url)
            if final_key in final_pages:
                continue
            final_pages.add(final_key)
            # Careers links commonly live in navigation/footer elements. Keep
            # them before removing boilerplate from text used for matching.
            links = [(link["href"], link.get_text(" ", strip=True)) for link in soup.find_all("a", href=True)]
            for tag in soup.find_all(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            headings = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
            title_match = next((h for h in headings if title and f" {title} " in f" {normalized(h)} "), None)
            location_match = bool(place and place in normalized(text))
            result["pages_checked"].append({"requested_url": requested, "url": final_url,
                "http_status": http_status,
                "heading_match": title_match, "location_match": location_match if place else None})
            successful += 1
            if title_match and (not place or location_match):
                result["matches"].append({"url": final_url, "heading_quote": title_match,
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
                if (careers_link(target, label)
                        or (title and title in normalized(label))):
                    if target not in visited and target not in queue:
                        if title and title in normalized(label):
                            if len(queue) >= 12:
                                queue.pop()
                            queue.appendleft(target)
                        elif len(queue) < 12:
                            queue.append(target)
            # A homepage may hide careers links behind JavaScript. Probe only
            # two common paths on that same origin, recording this limitation.
            if not queue and not urlsplit(final_url).path.strip("/") and requested == company_website:
                queue.extend([urljoin(final_url, "/careers"), urljoin(final_url, "/jobs")])
                result["limitations"].append("No careers link was discovered on the homepage; common /careers and /jobs paths were probed.")
        except (httpx.HTTPError, OSError, ValueError) as error:
            failure = {"url": requested, "message": str(error)}
            if isinstance(error, httpx.HTTPStatusError):
                failure["url"] = str(error.response.url)
                failure["requested_url"] = requested
                failure["http_status"] = error.response.status_code
            result["errors"].append(failure)
    if not result["matches"] and successful:
        result["status"] = "not_found_in_checked_pages"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company_website")
    parser.add_argument("job_title")
    parser.add_argument("--location")
    args = parser.parse_args()
    print(json.dumps(check_employer_association(args.company_website, args.job_title, args.location), indent=2))
