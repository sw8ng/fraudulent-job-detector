"""Run an assessment worker with a hard deadline and retain completed evidence."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from assessment_report import build_report
from saved_results import posting_fingerprint


def partial_report(job, model, progress, elapsed, status, reason):
    """A stopped workflow has no completed model verdict; report its actual limits."""
    progress.setdefault("model", model)
    progress.setdefault("input_fingerprint", posting_fingerprint(job))
    progress.setdefault("tool_calls", [])
    progress.setdefault("content_analysis", {})
    progress.setdefault("company_website_used", job.get("company_website"))
    progress.setdefault("employer_association", {"status": "not_requested", "matches": []})
    performance = progress.setdefault("performance", {})
    performance["total_seconds"] = round(elapsed, 3)
    performance["timing_incomplete"] = True
    calls = performance.setdefault("model_calls", [])
    for call in calls:
        if call.get("status") == "running":
            call["status"] = status
            call["seconds"] = round(max(0, time.time() - call.get("started_at", time.time())), 3)
            call["timing_incomplete"] = True
    progress["summary_review"] = {"status": "partial_evidence_only", "reason": reason}
    message = reason + " Completed evidence is retained; the final model assessment did not finish. Unfinished checks and missing evidence do not establish fraud or legitimacy."
    report = build_report(job, {"risk_level": "insufficient evidence", "explanation": message}, progress)
    report["execution_status"] = status
    report["stop_reason"] = reason
    return report


def run_bounded_assessment(job, *, model, max_seconds=300):
    """Terminate only this worker on expiry, never the shared Ollama server."""
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="job-assessment-") as folder:
        folder = Path(folder)
        input_file = folder / "input.json"
        output_file = folder / "output.json"
        progress_file = folder / "progress.json"
        input_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        command = [sys.executable, str(Path(__file__).with_name("assess_job.py")), str(input_file),
                   "--model", model, "--output", str(output_file), "--worker",
                   "--progress-file", str(progress_file)]
        status = "failed"
        reason = "Assessment worker stopped before completion."
        try:
            # subprocess.run kills and waits for its child when this deadline expires.
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=max_seconds)
            if result.returncode == 0 and output_file.is_file():
                return json.loads(output_file.read_text(encoding="utf-8"))
            if result.stderr.strip():
                reason += " " + result.stderr.strip().splitlines()[-1][:300]
        except subprocess.TimeoutExpired:
            status = "timed_out"
            reason = f"Assessment stopped at the {max_seconds:g}-second time limit."
        except (OSError, ValueError) as error:
            reason += " " + str(error)[:300]
        progress = {}
        if progress_file.is_file():
            try:
                progress = json.loads(progress_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return partial_report(job, model, progress, time.perf_counter() - started, status, reason)
