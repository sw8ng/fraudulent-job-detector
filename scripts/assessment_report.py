"""Build a readable assessment from recorded evidence, without inventing checks."""


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
        "company_identity": claim("not_checked", "No independent company identity verification tool has run."),
        "company_website": claim("not_checked", "No company website was supplied or selected."),
        "job_on_company_careers_page": claim("not_checked", "Employer association was not executed."),
        "recruiter_affiliation": claim("not_checked", "Recruiter affiliation verification is not implemented."),
        "application_domain": claim("not_checked", "No successful application URL trace was recorded."),
    }
    lookup = investigation.get("company_lookup")
    if lookup:
        evidence("company-search-1", "company_discovery", lookup)
        checks["company_website"] = claim("unresolved", "Search candidates do not independently verify the employer's website.", ["company-search-1"])
    if investigation.get("company_website_used"):
        checks["company_website"] = claim("unresolved", "A supplied or selected candidate website is available, but its ownership/association with the claimed employer is unverified.",
            ["company-search-1"] if lookup else ["posting-1"])
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
        if call.get("tool") != "inspect_application_url":
            continue
        result = call.get("evidence", {})
        identifier = evidence(f"url-{index}", "url_inspection", result, result.get("final_url"))
        if result.get("error"):
            checks["application_domain"] = claim("not_checked", "The URL tool request was rejected; no destination check executed for this request.", [identifier])
        elif not result.get("redirect_lookup_success"):
            checks["application_domain"] = claim("unresolved", "The URL check ran but its destination could not be established.", [identifier])
        elif result.get("domain_matches_company") is True:
            checks["application_domain"] = claim("supported", "The observed destination shares the candidate company website's registrable domain; this does not verify identity or page content.", [identifier])
            findings["supporting"].append({"finding": checks["application_domain"]["reason"], "evidence_ids": [identifier]})
        else:
            checks["application_domain"] = claim("unresolved", "The destination's association with the employer is unknown. A different domain may be a legitimate hiring platform, not a proven contradiction.", [identifier])
    unresolved = [{"claim": name, "status": check["status"], "reason": check["reason"], "evidence_ids": check["evidence_ids"]}
                  for name, check in checks.items() if check["status"] == "unresolved"]
    not_checked = [{"claim": name, "status": check["status"], "reason": check["reason"], "evidence_ids": check["evidence_ids"]}
                   for name, check in checks.items() if check["status"] == "not_checked"]
    action = ("Pause the application and independently verify the employer before paying or sharing sensitive information."
              if findings["suspicious"] else
              "Confirm the posting and application destination through an independently established employer channel before sharing sensitive information. This assessment does not verify legitimacy.")
    report = {"assessment": {"risk_level": assessment["risk_level"], "summary": assessment["explanation"]},
              "claim_checks": checks, "key_findings": findings, "unresolved": unresolved, "not_checked": not_checked,
              "evidence_sources": sources, "recommended_action": action, "investigation": investigation}
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
