"""Find a likely company website while keeping search provenance and uncertainty."""

import argparse
import json
import re
import unicodedata
from urllib.parse import urlsplit

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from inspect_application_url import EXTRACT, domain_for


DIRECTORIES = {
    "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "crunchbase.com",
    "trustpilot.com", "reddit.com", "wikipedia.org", "youtube.com",
    "zoominfo.com", "pitchbook.com", "yelp.com", "bbb.org",
    "g2.com", "saasworthy.com", "capterra.com", "getapp.com", "softwareadvice.com",
}


def search_web(query):
    """Normalize DDGS search results to the company lookup's evidence format."""
    results = DDGS(timeout=15).text(query, max_results=5, backend="auto")
    return [{"url": row.get("href", ""), "title": row.get("title", ""),
             "description": row.get("body", "")} for row in results[:5]]


def normalize(name):
    text = unicodedata.normalize("NFKD", name.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Ignore legal suffixes, preserving meaningful words such as Technologies.
    text = re.sub(r"(?:\s|,)+(?:incorporated|inc\.?|llc|ltd\.?|limited|corp\.?|corporation)\s*$", "", text)
    return "".join(c for c in text if c.isalnum())


def name_match_reasons(company, domain, title):
    name = normalize(company)
    if not name:
        return []
    reasons = []
    if name == normalize(EXTRACT(domain).domain):
        reasons.append("company_name_matches_domain")
    # Brand names often appear at the END of a title, e.g. About Us | Acme.
    segments = re.split(r"\s*[|–—:]\s*|\s+-\s+", title)
    if any(name == normalize(segment) for segment in segments):
        reasons.append("company_name_matches_title_segment")
    return reasons


def find_company_website(company: str, *, location=None, job_title=None,
                         company_description=None, searcher=None):
    """Search by the extracted employer name; never certify a result as official."""
    result = {"company": company, "query": None, "provider": "ddgs",
              "company_website": None, "status": "not_found", "verified": False,
              "candidates": [], "searches": [], "errors": []}
    if not isinstance(company, str) or not company.strip():
        result["status"] = "missing_company"
        return result
    query = f'"{company.strip().replace(chr(34), "")}" official company website'
    result["query"] = query
    try:
        rows = (searcher or search_web)(query)
        result["searches"].append({"query": query, "results": rows[:5]})
        candidates = {}
        for row in rows[:5]:
            url = row.get("url", "")
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
                    continue
                domain = domain_for(url)
            except (ValueError, UnicodeError, TypeError):
                continue
            if domain in DIRECTORIES:
                continue
            title = str(row.get("title") or "")[:500]
            description = str(row.get("description") or "")[:1500]
            # Prefer a company name matching the domain or the title's first segment.
            reasons = name_match_reasons(company, domain, title)
            match = bool(reasons)
            candidate = {"website": f"{parsed.scheme}://{parsed.netloc}/",
                         "source_url": url, "title": title, "description": description,
                         "name_match": match, "name_match_reasons": reasons, "evidence": [{"query": query, "source_url": url,
                         "title": title, "description": description}]}
            if domain not in candidates:
                candidates[domain] = candidate
            else:
                existing = candidates[domain]
                evidence = existing["evidence"] + candidate["evidence"]
                if match and not existing["name_match"]:
                    candidates[domain] = candidate
                candidates[domain]["evidence"] = evidence
        result["candidates"] = list(candidates.values())
        matches = [c for c in result["candidates"] if c["name_match"]]
        if len(matches) == 1:
            result["company_website"] = matches[0]["website"]
            result["status"] = "candidate_found"
        elif len(matches) > 1 or result["candidates"]:
            result["status"] = "ambiguous"
        context = [value.strip()[:160].replace('"', '') for value in
                   (location, job_title, company_description) if isinstance(value, str) and value.strip()]
        if result["status"] in ("ambiguous", "not_found") and context:
            contextual_query = f'"{company.strip().replace(chr(34), "")}" ' + " ".join(context[:2])
            try:
                extra_rows = (searcher or search_web)(contextual_query)
                result["searches"].append({"query": contextual_query, "results": extra_rows[:5]})
                # Apply identical candidate filtering without triggering another search.
                extra = find_company_website(company, searcher=lambda _: extra_rows)
                for candidate in extra["candidates"]:
                    for evidence in candidate["evidence"]:
                        evidence["query"] = contextual_query
                    domain = domain_for(candidate["website"])
                    if domain in candidates:
                        candidates[domain]["evidence"].extend(candidate["evidence"])
                        candidates[domain]["name_match"] |= candidate["name_match"]
                        candidates[domain]["name_match_reasons"] = list(dict.fromkeys(
                            candidates[domain]["name_match_reasons"] + candidate["name_match_reasons"]))
                    else:
                        candidates[domain] = candidate
                result["candidates"] = list(candidates.values())
            except (DDGSException, OSError, ValueError, TypeError, AttributeError) as error:
                result["errors"].append(f"Contextual search failed: {error}")
    except (DDGSException, OSError, ValueError, TypeError, AttributeError) as error:
        result["status"] = "lookup_failed"
        result["errors"].append(str(error))
    if result["status"] != "lookup_failed":
        matches = [c for c in result["candidates"] if c["name_match"]]
        result["company_website"] = matches[0]["website"] if len(matches) == 1 else None
        result["status"] = ("candidate_found" if len(matches) == 1 else
                            "ambiguous" if len(matches) > 1 else "no_matching_candidate")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company")
    parser.add_argument("--location")
    parser.add_argument("--job-title")
    parser.add_argument("--company-description")
    args = parser.parse_args()
    print(json.dumps(find_company_website(args.company, location=args.location,
        job_title=args.job_title, company_description=args.company_description), indent=2, ensure_ascii=False))
