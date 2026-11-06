"""Small file checks used before starting expensive model or network work."""

import hashlib
import json

from assessment_report import validate_evidence_references


def posting_fingerprint(job):
    """Give the same posting data the same short identity, regardless of key order."""
    text = json.dumps(job, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_report(path, model):
    """Read a completed report; missing, damaged or wrong-model files cannot be reused."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report["investigation"]["model"] != model:
            return None
        if report["assessment"]["risk_level"] not in ("low", "medium", "high", "insufficient evidence"):
            return None
        if not isinstance(report["assessment"]["summary"], str):
            return None
        validate_evidence_references(report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return report


def matching_report(path, job, model):
    """Standalone assessments also need a matching input fingerprint."""
    report = read_report(path, model)
    if report is None:
        return None
    saved_fingerprint = report["investigation"].get("input_fingerprint")
    if saved_fingerprint != posting_fingerprint(job):
        return None
    return report
