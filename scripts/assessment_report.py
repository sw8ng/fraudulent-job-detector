"""Build scoped claim results from actual tool evidence."""

from check_employer_association import page_key


def same_page(left, right):
    """Compare exact job pages, ignoring only the existing tracking-key rules."""
    if not left or not right:
        return False
    try:
        return page_key(left) == page_key(right)
    except ValueError:
        return False



def build_report(job, assessment, investigation):
    sources = []
    findings = {"suspicious": [], "supporting": []}

    def evidence(identifier, kind, data, url=None):
        sources.append({"id": identifier, "type": kind, "url": url, "evidence": data})
        return identifier

    def claim(status, reason, ids=None):
        return {"status": status, "reason": reason, "evidence_ids": ids or []}

    evidence("posting-1", "supplied_posting", {
        key: job.get(key) for key in ("source_file", "company", "job_title", "location", "company_website", "application_url")}, job.get("source_url"))
    checks = {
        "company_domain_registration": claim("not_checked", "Company-domain registration was not checked."),
        "application_page_content": claim("not_checked", "Application-page collection was not executed."),
        "application_destination_association": claim("not_checked", "No destination association check was executed."),
        "job_on_company_careers_page": claim("not_checked", "Employer association was not executed."),
        "email_domain_consistency": claim("not_checked", "No posting email-domain comparison ran."),
        "application_domain": claim("not_checked", "No successful application URL trace was recorded."),
    }
    lookup = investigation.get("company_lookup")
    if lookup:
        evidence("company-search-1", "company_discovery", lookup)
    content = investigation.get("content_analysis") or {}
    for index, observation in enumerate(content.get("observations", []), 1):
        identifier = evidence(f"content-{index}", "quoted_content_observation", observation)
        if observation.get("assessment") == "concern":
            findings["suspicious"].append({"finding": observation["interpretation"], "evidence_ids": [identifier]})
    association = investigation.get("employer_association") or {}
    if association.get("status") not in (None, "not_requested"):
        evidence("association-1", "employer_association", association, association.get("company_website"))
        if association.get("status") == "matching_posting_found" and association.get("matches"):
            reason = "A matching heading/location was found on the candidate site or a hiring page linked from it; employer identity remains unverified."
            checks["job_on_company_careers_page"] = claim("supported", reason, ["association-1"])
            findings["supporting"].append({"finding": reason, "evidence_ids": ["association-1"]})
        else:
            checks["job_on_company_careers_page"] = claim("unresolved", "The bounded check did not establish a match (" + str(association.get("status")) + "); absence or failed access is not a contradiction.", ["association-1"])
    for index, call in enumerate(investigation.get("tool_calls", []), 1):
        if call.get("tool") == "collect_application_page":
            result = call.get("evidence", {})
            identifier = evidence(f"application-page-{index}", "application_page_content", result, result.get("final_url"))
            if result.get("extraction_success") and result.get("text", "").strip():
                checks["application_page_content"] = claim("supported", "HTML text was collected from the recorded application destination. This confirms readable content, not a full job description or application form.", [identifier])
            elif checks["application_page_content"]["status"] != "supported":
                checks["application_page_content"] = claim("not_checked" if result.get("error") else "unresolved", "No readable destination text was collected; access failure does not establish fraud.", [identifier])
            continue
        if call.get("tool") == "get_company_domain_info":
            result = call.get("evidence", {})
            identifier = evidence(f"company-domain-{index}", "company_domain_registration", result, result.get("rdap_source_url"))
            if result.get("lookup_success") and result.get("domain_created"):
                checks["company_domain_registration"] = claim("supported", "Registry evidence returned a registration date for " + str(result.get("domain")) + ": " + str(result["domain_created"]) + ". This is domain history, not the company's founding date or identity verification.", [identifier])
            elif checks["company_domain_registration"]["status"] != "supported":
                checks["company_domain_registration"] = claim("not_checked" if result.get("error") else "unresolved", "Registration date was not established; lookup failure is not fraud evidence.", [identifier])
            continue
        if call.get("tool") == "check_recruiter_email":
            result = call.get("evidence", {})
            identifier = evidence(f"email-{index}", "recruiter_email_comparison", result)
            if result.get("status") == "checked" and result.get("emails"):
                comparisons = [email.get("comparison") for email in result["emails"]]
                if all(value == "matching_domain" for value in comparisons):
                    checks["email_domain_consistency"] = claim("supported", "All detected email mentions share the candidate company website's registrable domain. This supports domain consistency, not the sender's identity or recruiter affiliation.", [identifier])
                elif any(value in ("different_domain", "free_provider") for value in comparisons):
                    checks["email_domain_consistency"] = claim("different_domain" if result.get("company_domain") else "unresolved", "At least one email uses a different domain or free provider. Review each address and its source context; agency and administrative contacts may be legitimate.", [identifier])
                else:
                    checks["email_domain_consistency"] = claim("unresolved", "No valid candidate company domain or email domain was available for comparison.", [identifier])
            continue
        if call.get("tool") != "inspect_application_url":
            continue
        result = call.get("evidence", {})
        identifier = evidence(f"url-{index}", "url_inspection", result, result.get("final_url"))
        if result.get("error") and checks["application_domain"]["status"] != "not_checked":
            continue
        if result.get("error"):
            checks["application_domain"] = claim("not_checked", "The URL tool request was rejected; no destination check executed for this request.", [identifier])
        elif not result.get("redirect_lookup_success"):
            checks["application_domain"] = claim("unresolved", "The URL check ran but its destination could not be established.", [identifier])
        elif result.get("domain_matches_company") is True:
            checks["application_domain"] = claim("supported", "The observed destination shares the candidate company website's registrable domain; this does not verify identity or page content.", [identifier])
            findings["supporting"].append({"finding": checks["application_domain"]["reason"], "evidence_ids": [identifier]})
        elif result.get("domain_matches_company") is False:
            checks["application_domain"] = claim("different_domain", "The observed destination and candidate company website have different registrable domains. This is a completed domain comparison; an external hiring platform may still be associated with the company.", [identifier])
        else:
            checks["application_domain"] = claim("unresolved", "The destination was observed, but no valid candidate company domain was available for comparison.", [identifier])
    for index, call in enumerate(investigation.get("tool_calls", []), 1):
        if call.get("tool") != "inspect_application_url":
            continue
        result = call.get("evidence", {})
        if result.get("error") or not result.get("redirect_lookup_success"):
            continue
        identifier = f"url-{index}"
        destination = result.get("final_url")
        if result.get("domain_matches_company") is True:
            checks["application_destination_association"] = claim("supported", "The observed destination shares the candidate company domain. This supports association with that candidate, not independent employer identity.", [identifier])
            break
        linked = any(same_page(link.get("to_url"), destination) for link in association.get("discovered_links", []))
        matched = any(same_page(match.get("url"), destination) for match in association.get("matches", []))
        if association.get("status") not in (None, "not_requested") and (linked or matched):
            checks["application_destination_association"] = claim("supported", "The exact application destination was linked or matched through the candidate company's inspected careers path, including an external hiring platform.", [identifier, "association-1"])
            break
        checks["application_destination_association"] = claim("unresolved", "No recorded company careers link or matching job connects this exact external destination to the candidate company. A different domain alone is not fraud evidence.", [identifier])
    for name in ("company_domain_registration", "application_page_content", "email_domain_consistency", "application_destination_association"):
        check = checks[name]
        if check["status"] == "supported":
            findings["supporting"].append({"finding": check["reason"], "evidence_ids": check["evidence_ids"]})
    unresolved = [{"claim": name, "status": check["status"], "reason": check["reason"], "evidence_ids": check["evidence_ids"]}
                  for name, check in checks.items() if check["status"] == "unresolved"]
    not_checked = [{"claim": name, "status": check["status"], "reason": check["reason"], "evidence_ids": check["evidence_ids"]}
                   for name, check in checks.items() if check["status"] == "not_checked"]
    action = ("Pause the application and independently verify the employer before paying or sharing sensitive information."
              if findings["suspicious"] else
              "Confirm the posting and application destination through an independently established employer channel before sharing sensitive information. This assessment does not verify legitimacy.")
    report = {"assessment": {"risk_level": assessment["risk_level"], "summary": assessment["explanation"]},
              "claim_checks": checks, "key_findings": findings, "unresolved": unresolved, "not_checked": not_checked,
              "evidence_sources": sources, "recommended_action": action,
              "candidate_company_website": investigation.get("company_website_used"),
              "verification_limits": ["Company identity, website ownership and recruiter identity are not independently authenticated by these tools. Supported results apply only to the specific claims above."],
              "investigation": investigation}
    validate_evidence_references(report)
    return report


def validate_evidence_references(report):
    ids = {source["id"] for source in report["evidence_sources"]}
    if len(ids) != len(report["evidence_sources"]):
        raise ValueError("Duplicate evidence source IDs")
    entries = list(report["claim_checks"].values()) + report["unresolved"] + report.get("not_checked", [])
    entries += report["key_findings"]["suspicious"] + report["key_findings"]["supporting"]
    for entry in entries:
        if any(identifier not in ids for identifier in entry["evidence_ids"]):
            raise ValueError("Assessment references an unknown evidence source")
