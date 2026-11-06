"""Scope observed facts and check a few mechanically verifiable summary errors."""

import re
from check_employer_association import page_key


def observed_facts(investigation):
    urls = []
    for call in investigation.get("tool_calls", []):
        if call.get("tool") != "inspect_application_url":
            continue
        result = call.get("evidence", {})
        if not result.get("redirect_lookup_success"):
            continue
        urls.append({"final_url": result.get("final_url"), "final_domain": result.get("final_domain"),
                     "observed_http_redirect_count": len(result.get("redirects", [])),
                     "http_status": result.get("http_status"),
                     "company_domain": result.get("company_domain"),
                     "domain_matches_candidate": result.get("domain_matches_company")})
    association = investigation.get("employer_association") or {}
    website = investigation.get("company_website_used")
    website_statuses = [p["http_status"] for p in association.get("pages_checked", [])
                        if p.get("http_status") and website and page_key(p["url"]) == page_key(website)]
    return {"url_observations": urls,
            "application_page_observations": [call["evidence"] for call in investigation.get("tool_calls", [])
                if call.get("tool") == "collect_application_page" and not call.get("evidence", {}).get("error")],
            "content_concern_quotes": [item["quote"] for item in (investigation.get("content_analysis") or {}).get("observations", [])
                                       if item.get("assessment") == "concern" and item.get("quote")],
            "company_website_http_statuses": website_statuses,
            "company_domain_observations": [call["evidence"] for call in investigation.get("tool_calls", [])
                if call.get("tool") == "get_company_domain_info" and not call.get("evidence", {}).get("error")],
            "association_page_results": association.get("pages_checked", []),
            "association_page_errors": association.get("errors", []),
            "company_website_is_unverified": True,
            "company_website_used": investigation.get("company_website_used"),
            "association_status": association.get("status", "not_requested"),
            "recruiter_email_checks": [call["evidence"] for call in investigation.get("tool_calls", [])
                                       if call.get("tool") == "check_recruiter_email" and not call.get("evidence", {}).get("error")],
            "association_pages_checked": [p["url"] for p in association.get("pages_checked", [])],
            "association_matches": association.get("matches", [])}


def summary_issues(summary, facts):
    issues = []
    if any(200 <= code < 300 for code in facts.get("company_website_http_statuses", [])):
        pattern = r"\b(?:company\s+(?:website|site|homepage)|homepage)\b([^.!?;]{0,120})\b(?:404|unavailable|inaccessible)\b([^.!?;]{0,80})"
        if any(not re.search(r"\bcareers?\b|/jobs|\bpaths\b|\bapplication\b", match.group(1) + match.group(2), re.I)
               for match in re.finditer(pattern, summary, re.I)):
            issues.append("The candidate company webpage was successfully read; errors on application or careers paths must not be attributed to that webpage.")
    if facts.get("association_status") in (None, "not_requested") and re.search(r"\bno\b[^.!?]{0,80}\b(?:job listing|matching (?:job|posting))\b[^.!?]{0,30}\bfound\b", summary, re.I):
        issues.append("No employer-association check ran; do not describe an unperformed search as a missing job match.")
    if re.search(r"\bdefinitive\b[^.!?]{0,40}\b(?:fraud|scam)\b", summary, re.I):
        issues.append("Describe observed requests and assessed risk without claiming definitive proof of fraud.")
    urls = facts["url_observations"]
    for registry in facts.get("company_domain_observations", []):
        date = registry.get("domain_created")
        if not registry.get("lookup_success") or not date:
            continue
        for url in urls:
            domain = url.get("final_domain")
            if domain and domain != registry.get("domain"):
                pattern = re.escape(domain) + r"[^.!?;]{0,50}\b(?:registered|created)\b[^.!?;]{0,25}" + re.escape(date[:4])
                if re.search(pattern, summary, re.I):
                    issues.append("Company registration history is attributed to a different application domain that was not investigated for registration.")
    page_redirects_observed = False
    for page in facts.get("application_page_observations", []):
        if page.get("redirects"):
            page_redirects_observed = True
    if urls and all(u["observed_http_redirect_count"] == 0 for u in urls) and not page_redirects_observed:
        for match in re.finditer(r"\bredirect(?:s|ed|ing)?\s+(?:to|through|toward)\b", summary, re.I):
            preceding = summary[max(0, match.start() - 35):match.start()]
            if not re.search(r"\b(?:no|not|never|without)\b[^.!?]*$", preceding, re.I):
                issues.append("Summary claims an HTTP redirect despite zero observed HTTP redirects.")
                break
    for match in re.finditer(r"\b(?:official|verified|legitimate|trusted|authentic)\s+(?:company\s+)?(?:site|website|domain)\b", summary, re.I):
        preceding = summary[max(0, match.start() - 50):match.start()]
        if not re.search(r"\b(?:no|not|never|without|unverified)\b[^.!?]*$", preceding, re.I):
            issues.append("A candidate website is described as official/verified; website identity was not independently verified.")
            break
    return issues


def factual_summary(facts):
    """Conservative fallback for detected errors that persist after one retry."""
    parts = []
    for quote in facts.get("content_concern_quotes", [])[:3]:
        parts.append("Quoted posting concern: " + quote)
    for item in facts.get("company_domain_observations", []):
        if item.get("lookup_success"):
            parts.append(f"The separate company-domain lookup for {item['domain']} returned registration date {item.get('domain_created')} and age {item.get('domain_age_days')} days; this does not verify employer identity.")
        else:
            parts.append("The separate company-domain registration lookup did not return registration evidence.")
    for item in facts["url_observations"]:
        parts.append(f"URL inspection observed {item['observed_http_redirect_count']} HTTP redirects and a destination on {item['final_domain']} (HTTP {item['http_status']}).")
    for page in facts.get("application_page_observations", []):
        if page.get("extraction_success"):
            parts.append("Application destination HTML text was collected; see its recorded URL and text. This does not establish that an application form was reached or verify legitimacy.")
        else:
            parts.append("Application page collection was unavailable (" + str(page.get("reason")) + "); this is not fraud evidence.")
    status = facts["association_status"]
    if status == "matching_posting_found":
        parts.append("A matching posting was found on the candidate site or a page linked from it; this supports association only.")
    elif status not in (None, "not_requested"):
        parts.append(f"Employer association returned {status} after checking {len(facts['association_pages_checked'])} HTML pages; this does not establish fraud.")
    parts.append("Company identity and website ownership remain unverified. Review the quoted findings and checks for the basis of the model's risk level.")
    return " ".join(parts)
