"""Deterministic quality checks for the existing saved comparison format."""

import json
from copy import deepcopy
from pathlib import Path

from jsonschema import validate, ValidationError
from assessment_report import validate_evidence_references
from saved_results import posting_fingerprint
from summary_grounding import observed_facts, summary_issues

EVALUATOR_VERSION = "2"
FINDING_IDS = ["posting_closed", "application_page_unavailable", "no_http_redirects",
               "application_domain_matches", "application_domain_differs",
               "company_registration_observed", "applicant_payment_request", "job_match_found"]
FINDINGS_SCHEMA = {"type": "array", "maxItems": 8, "items": {
    "type": "object", "additionalProperties": False,
    "properties": {"id": {"type": "string", "enum": FINDING_IDS},
                   "quote": {"type": "string"},
                   "evidence_ids": {"type": "array", "minItems": 1, "uniqueItems": True,
                                    "items": {"type": "string"}}},
    "required": ["id", "quote", "evidence_ids"]}}
TOOL_ITEM = {"oneOf": [{"type": "string", "minLength": 1}, {"type": "object",
    "properties": {"name": {"type": "string", "minLength": 1}, "when": {"type": "object"}},
    "required": ["name"], "additionalProperties": False}]}
TRUTH_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {
        "schema_version": {"const": 1}, "case_id": {"type": "string", "minLength": 1},
        "input_fingerprint": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "notes": {"type": "string"},
        "expected_tools": {"type": "array", "items": TOOL_ITEM},
        "useful_tools": {"type": "array", "items": TOOL_ITEM},
        "important_findings": {"type": "array", "items": {"type": "object",
            "properties": {"id": {"type": "string", "enum": FINDING_IDS},
                "evidence_type": {"type": "string"}, "source_quote": {"type": "string"},
                "when": {"type": "object"}}, "required": ["id"], "additionalProperties": False}},
        "unsupported_claims": {"type": "array", "items": {"type": "object",
            "properties": {"id": {"type": "string"}, "rule": {"enum": [
                "redirect_claim_without_observed_redirect", "open_claim_with_closed_evidence", "forbidden_finding", "structured_mismatch"]},
                "finding_id": {"type": "string", "enum": FINDING_IDS},
                "path": {"type": "string"}, "equals": {}},
            "required": ["id", "rule"], "additionalProperties": False}},
        "structured_expectations": {"type": "array", "items": {"type": "object",
            "properties": {"path": {"type": "string"}, "equals": {}},
            "required": ["path", "equals"], "additionalProperties": False}}},
    "required": ["schema_version", "case_id", "input_fingerprint", "expected_tools",
                 "useful_tools", "important_findings", "unsupported_claims"]}


def normalized(text):
    return " ".join(text.casefold().split())


def field_value(data, path):
    """Only dictionary keys/list indexes; never execute expressions from a case file."""
    for key in path.split("."):
        if isinstance(data, dict) and key in data:
            data = data[key]
        elif isinstance(data, list) and key.isdigit() and int(key) < len(data):
            data = data[int(key)]
        else:
            return False, None
    return True, data


def evidence_text(source):
    data = source.get("evidence", {})
    if isinstance(data, dict):
        return str(data.get("text") or data.get("quote") or "")
    return ""


def applies(report, condition):
    if not condition:
        return True
    fields = set(condition)
    if fields not in ({"path", "equals"}, {"evidence_type", "field", "equals"}, {"evidence_type", "source_quote"}, {"path", "exists"}):
        raise ValueError("Unknown or incomplete applicability condition")
    if "path" in condition and not isinstance(condition["path"], str):
        raise ValueError("Condition path must be a string")
    if "field" in condition and not isinstance(condition["field"], str):
        raise ValueError("Condition field must be a string")
    if "path" in condition and "exists" in condition:
        if not isinstance(condition["exists"], bool):
            raise ValueError("Condition exists must be true or false")
        present, value = field_value(report, condition["path"])
        return (present and value is not None and value != "") == condition["exists"]
    if "path" in condition and "equals" in condition:
        present, value = field_value(report, condition["path"])
        return present and value == condition["equals"]
    if "evidence_type" in condition and "field" in condition and "equals" in condition:
        for source in report.get("evidence_sources", []):
            present, value = field_value(source.get("evidence", {}), condition["field"])
            if source.get("type") == condition["evidence_type"] and present and value == condition["equals"]:
                return True
        return False
    if "evidence_type" in condition and "source_quote" in condition and isinstance(condition["source_quote"], str) and condition["source_quote"].strip():
        return any(source.get("type") == condition["evidence_type"] and
                   normalized(condition["source_quote"]) in normalized(evidence_text(source))
                   for source in report.get("evidence_sources", []))
    raise ValueError("A condition needs path/equals or evidence_type/source_quote")


def load_ground_truth(directory):
    cases = {}
    for path in sorted(Path(directory).glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        try:
            validate(case, TRUTH_SCHEMA)
        except ValidationError as error:
            raise ValueError(str(path) + ": " + error.message) from error
        for field in ("expected_tools", "useful_tools", "important_findings", "unsupported_claims"):
            ids = [item if isinstance(item, str) else item.get("name", item.get("id")) for item in case[field]]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate entries in " + str(path) + ": " + field)
        expected = {item if isinstance(item, str) else item["name"] for item in case["expected_tools"]}
        useful = {item if isinstance(item, str) else item["name"] for item in case["useful_tools"]}
        if not expected.issubset(useful):
            raise ValueError("Expected tools must also be listed as useful: " + str(path))
        for item in case["expected_tools"] + case["useful_tools"] + case["important_findings"]:
            if isinstance(item, dict) and "when" in item:
                applies({}, item["when"])
        for rule in case["unsupported_claims"]:
            if rule["rule"] == "forbidden_finding" and "finding_id" not in rule:
                raise ValueError("forbidden_finding needs finding_id")
            if rule["rule"] == "structured_mismatch" and not {"path", "equals"}.issubset(rule):
                raise ValueError("structured_mismatch needs path and equals")
        fingerprint = case["input_fingerprint"]
        if fingerprint in cases:
            raise ValueError("More than one ground-truth case for the same posting")
        cases[fingerprint] = case
    if not cases:
        raise ValueError("No ground-truth JSON files found in " + str(directory))
    return cases


def finding_supported(report, finding, expectation=None):
    """Validate the typed fact and its own evidence, not just the presence of a tag."""
    sources = {source["id"]: source for source in report.get("evidence_sources", [])}
    ids = finding.get("evidence_ids", [])
    if not ids or any(identifier not in sources for identifier in ids):
        return False
    cited = [sources[identifier] for identifier in ids]
    quote = finding.get("quote", "")
    if expectation:
        cited = [source for source in cited if not expectation.get("evidence_type") or
                 source.get("type") == expectation["evidence_type"]]
        if expectation.get("source_quote"):
            cited = [source for source in cited if normalized(expectation["source_quote"]) in normalized(evidence_text(source))]
    if not cited:
        return False
    if quote and not any(normalized(quote) in normalized(evidence_text(source)) for source in cited):
        return False
    for source in cited:
        kind, data = source.get("type"), source.get("evidence", {})
        name = finding.get("id")
        if name == "posting_closed" and kind == "application_page_content":
            if data.get("extraction_success") and quote and any(phrase in normalized(quote) for phrase in (
                    "no longer available", "position is closed", "job has expired", "applications are closed")):
                return True
        if name == "application_page_unavailable" and kind in ("url_inspection", "application_page_content"):
            if (data.get("http_status") or 0) >= 400:
                return True
            if kind == "application_page_content" and data.get("extraction_success") is False and data.get("reason"):
                return True
        if name == "job_match_found" and kind == "employer_association":
            if data.get("status") == "matching_posting_found" and data.get("matches"):
                return True
        if kind == "url_inspection" and data.get("redirect_lookup_success"):
            if name == "no_http_redirects" and data.get("redirects") == []:
                return True
            if name == "application_domain_matches" and data.get("domain_matches_company") is True:
                return True
            if name == "application_domain_differs" and data.get("domain_matches_company") is False:
                return True
        if name == "company_registration_observed" and kind == "company_domain_registration":
            if data.get("lookup_success") and data.get("domain_created"):
                return True
        if name == "applicant_payment_request" and kind == "quoted_content_observation":
            if quote and data.get("assessment") == "concern" and data.get("category") == "payment":
                return True
    return False


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def evaluate_report(report, case):
    result = {"status": "evaluated", "evaluator_version": EVALUATOR_VERSION,
              "case_id": case["case_id"], "ground_truth_version": case["schema_version"],
              "ground_truth_fingerprint": posting_fingerprint(case),
              "target": "final_pipeline_assessment", "validation_flags": []}
    investigation = report.get("investigation", {})
    if investigation.get("input_fingerprint") != case["input_fingerprint"]:
        return {**result, "status": "input_mismatch"}
    if report.get("execution_status", "completed") != "completed":
        return {**result, "status": "incomplete_assessment"}
    expected, useful, skipped = set(), set(), []
    for field, selected in (("expected_tools", expected), ("useful_tools", useful)):
        for item in case[field]:
            name = item if isinstance(item, str) else item["name"]
            if isinstance(item, str) or applies(report, item.get("when")):
                selected.add(name)
            else:
                skipped.append(name)
    # A required useful check cannot become a false positive through a narrower useful condition.
    useful.update(expected)
    calls = investigation.get("tool_calls", [])
    selected = set()
    for call in calls:
        if not call.get("evidence", {}).get("error"):
            selected.add(call["tool"])
    result["tool_recall"] = ratio(len(selected & expected), len(expected))
    result["tool_precision"] = ratio(len(selected & useful), len(calls))
    result["missing_tools"] = sorted(expected - selected)
    result["other_selected_tools"] = sorted(selected - useful)
    result["inapplicable_tools"] = sorted(set(skipped))
    findings = report.get("assessment", {}).get("findings")
    if findings is not None:
        validate(findings, FINDINGS_SCHEMA)
    expected_findings = [item for item in case["important_findings"] if applies(report, item.get("when"))]
    result["finding_results"] = []
    if findings is None:
        result["finding_recall"] = None
        result["finding_status"] = report.get("findings_status", "legacy_missing_finding_ids")
    else:
        for item in expected_findings:
            retained = any(finding.get("id") == item["id"] and finding_supported(report, finding, item)
                           for finding in findings)
            result["finding_results"].append({"id": item["id"], "retained": retained})
        result["finding_recall"] = ratio(sum(item["retained"] for item in result["finding_results"]), len(expected_findings))
        result["finding_status"] = "scored"
    violations = {}
    for finding in findings or []:
        if not finding_supported(report, finding):
            violations["finding:" + finding.get("id", "unknown")] = "Finding has missing, contradictory or unsupported evidence"
    try:
        validate_evidence_references(report)
    except (ValueError, KeyError, TypeError) as error:
        result["validation_flags"].append(str(error))
    facts = observed_facts(investigation)
    flags = summary_issues(report["assessment"]["summary"], facts)
    result["validation_flags"].extend(flags)
    checked_claim_rules = len(findings or [])
    unavailable_claim_rules = []
    for rule in case["unsupported_claims"]:
        if rule["rule"] == "redirect_claim_without_observed_redirect":
            if not facts["url_observations"]:
                unavailable_claim_rules.append(rule["id"])
                continue
            checked_claim_rules += 1
            if "Summary claims an HTTP redirect despite zero observed HTTP redirects." in flags:
                violations[rule["id"]] = "Summary claims a redirect but the recorded inspection observed none"
        elif rule["rule"] == "open_claim_with_closed_evidence":
            if not any(page.get("extraction_success") and "this posting is no longer available" in normalized(page.get("text", ""))
                       for page in facts.get("application_page_observations", [])):
                unavailable_claim_rules.append(rule["id"])
                continue
            checked_claim_rules += 1
            if "Summary claims an open or unexpired job despite an explicit closed-posting notice in collected text." in flags:
                violations[rule["id"]] = "Summary contradicts the recorded closed-posting notice"
        elif rule["rule"] == "forbidden_finding":
            if findings is None:
                unavailable_claim_rules.append(rule["id"])
                continue
            checked_claim_rules += 1
            if any(finding.get("id") == rule["finding_id"] for finding in findings or []):
                violations["finding:" + rule["finding_id"]] = rule["id"]
        elif rule["rule"] == "structured_mismatch":
            present, value = field_value(report, rule["path"])
            if not present:
                unavailable_claim_rules.append(rule["id"])
                continue
            checked_claim_rules += 1
            if value != rule["equals"]:
                violations[rule["id"]] = "Structured assertion differs from its ground-truth expectation"
    result["unsupported_claims"] = {"count": len(violations) if checked_claim_rules else None,
        "checks_available": checked_claim_rules, "checks_unavailable": unavailable_claim_rules, "violations": violations,
        "scope": "Known deterministic checks only; zero does not prove full faithfulness"}
    result["structured_expectations"] = []
    for item in case.get("structured_expectations", []):
        present, value = field_value(report, item["path"])
        result["structured_expectations"].append({"path": item["path"], "expected": item["equals"],
            "observed": value, "passed": present and value == item["equals"]})
    return result


def attach_quality(experiment, folder, ground_truth):
    """Enrich existing run rows; do not modify their saved reports or measurements."""
    result = deepcopy(experiment)
    cases = {case["source"]: case for case in result["cases"]}
    for row in result["runs"]:
        row.pop("quality", None)
        case = cases.get(row.get("source"))
        if case is None:
            matches = [case for case in cases.values() if case["name"] == row.get("case")]
            case = matches[0] if len(matches) == 1 else None
        fingerprint = posting_fingerprint(case["job"]) if case else None
        truth = ground_truth.get(fingerprint)
        if truth is None:
            row["quality"] = {"status": "missing_ground_truth"}
        elif row.get("status") != "completed":
            row["quality"] = {"status": "incomplete_assessment"}
        else:
            filename = row.get("report", "")
            if not filename or Path(filename).name != filename:
                row["quality"] = {"status": "invalid_report_path"}
                continue
            try:
                report = json.loads((Path(folder) / filename).read_text(encoding="utf-8"))
                if report["investigation"]["model"] != row["model"]:
                    raise ValueError("Report model does not match comparison")
                row["quality"] = evaluate_report(report, truth)
            except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
                row["quality"] = {"status": "invalid_report", "reason": str(error)}
    result["quality_evaluation"] = {"version": EVALUATOR_VERSION,
        "ground_truth": list(ground_truth.values()), "target": "final_pipeline_assessment"}
    return result
