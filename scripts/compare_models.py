"""Compare only the supplied postings and reuse matching completed assessments."""

import argparse
import json
import os
import time
from pathlib import Path

from ollama import Client
from assess_job import assess_job
from model_client import MeasuredClient
from performance_view import write_dashboard
from saved_results import posting_fingerprint, read_report


def evaluation_cases(inputs):
    """Read the user's files. No hidden or automatically added test postings."""
    cases = []
    sources = set()
    for path in inputs:
        source = str(path.resolve())
        if source in sources:
            raise ValueError("The same input was supplied twice: " + str(path))
        sources.add(source)
        job = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(job, dict):
            raise ValueError("Expected a posting JSON object in " + str(path))
        case = {
            "name": path.name,
            "kind": "saved_posting",
            "source": source,
            "job": job,
            "input_fingerprint": posting_fingerprint(job),
        }
        cases.append(case)
    return cases


def find_saved_run(previous, case, model, repetition, folder):
    """The saved input snapshot and model must match, and the report must still exist."""
    old_case = None
    for candidate in previous.get("cases", []):
        old_source = candidate.get("source")
        if old_source is None:
            continue
        if str(Path(old_source).resolve()) == case["source"] and candidate.get("job") == case["job"]:
            old_case = candidate
            break
    if old_case is None:
        return None

    # New rows have source paths. Older experiments used case names instead.
    for row in reversed(previous.get("runs", [])):
        if row.get("model") != model or row.get("repetition") != repetition:
            continue
        if row.get("status") != "completed":
            continue
        if row.get("source"):
            if row["source"] != case["source"]:
                continue
        else:
            if row.get("case") != old_case["name"]:
                continue
            # Old rows stored only a filename. Two identical filenames from
            # different folders cannot be safely distinguished in that format.
            same_name_count = 0
            for candidate in previous.get("cases", []):
                if candidate.get("name") == old_case["name"]:
                    same_name_count += 1
            if same_name_count > 1:
                continue
        filename = row.get("report")
        if not filename:
            continue
        report_path = (folder / filename).resolve()
        if report_path.parent != folder.resolve():
            continue
        report = read_report(report_path, model)
        if report is None:
            continue
        fingerprint = report["investigation"].get("input_fingerprint")
        if fingerprint is not None and fingerprint != case["input_fingerprint"]:
            continue
        saved = dict(row)
        saved["source"] = case["source"]
        saved["reused"] = True
        return saved
    return None


def add_report_metrics(row, report):
    """Keep the counts explicit so it is clear what each number measures."""
    investigation = report["investigation"]
    content = investigation["content_analysis"]
    raw_observations = content.get("raw_model_output", {}).get("observations", [])
    corrected_observations = content.get("observations", [])
    row["raw_concerns"] = 0
    for observation in raw_observations:
        if observation.get("assessment") == "concern":
            row["raw_concerns"] += 1
    row["corrected_concerns"] = 0
    for observation in corrected_observations:
        if observation.get("assessment") == "concern":
            row["corrected_concerns"] += 1
    row["quote_validation_errors"] = len(content.get("errors", []))
    row["tool_requests"] = 0
    row["tool_rejections"] = 0
    row["tools"] = []
    row["tool_seconds"] = 0
    for call in investigation["tool_calls"]:
        row["tool_requests"] += 1
        row["tools"].append(call["tool"])
        row["tool_seconds"] += call.get("seconds", 0)
        if "error" in call["evidence"]:
            row["tool_rejections"] += 1
    row["risk"] = report["assessment"]["risk_level"]
    row["summary_review"] = investigation["summary_review"]["status"]


def run_case(case, model, repetition, folder, index):
    """Run one missing assessment. A failed run is saved as a failure, never as reusable work."""
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    client = MeasuredClient(Client(host=host, timeout=300))
    row = {
        "case": case["name"], "kind": case["kind"], "source": case["source"],
        "model": model, "repetition": repetition, "status": "failed", "reused": False,
        "raw_concerns": None, "corrected_concerns": None,
    }
    started = time.perf_counter()
    try:
        report = assess_job(case["job"], model=model, client=client, include_trace=True)
        # Keep old report files even when --force requests a fresh measurement.
        filename = f"report-{index:03d}.json"
        while (folder / filename).exists():
            index += 1
            filename = f"report-{index:03d}.json"
        (folder / filename).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        row["report"] = filename
        add_report_metrics(row, report)
        row["status"] = "completed"
    except Exception as error:
        row["error"] = type(error).__name__ + ": " + str(error)
    row["seconds"] = round(time.perf_counter() - started, 3)
    row["model_calls"] = client.calls
    row["model_seconds"] = 0
    row["output_tokens"] = 0
    generation_seconds = 0
    for call in client.calls:
        row["model_seconds"] += call["seconds"]
        row["output_tokens"] += call.get("eval_count", 0)
        generation_seconds += call.get("eval_duration", 0) / 1e9
    row["model_seconds"] = round(row["model_seconds"], 3)
    row["tokens_per_second"] = None
    if generation_seconds > 0:
        row["tokens_per_second"] = round(row["output_tokens"] / generation_seconds, 2)
    return row


def save_experiment(experiment, folder):
    """Update the visible results after each case, including failures and reused reports."""
    text = json.dumps(experiment, indent=2, ensure_ascii=False)
    temporary_path = folder / "comparison.json.tmp"
    temporary_path.write_text(text, encoding="utf-8")
    temporary_path.replace(folder / "comparison.json")
    write_dashboard(experiment, folder / "comparison.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True, help="Installed Ollama model tags")
    input_options = parser.add_mutually_exclusive_group(required=True)
    input_options.add_argument("--inputs", nargs="+", type=Path, help="Assess these extracted posting JSON files")
    input_options.add_argument("--input-dir", type=Path, help="Assess every JSON file directly in this extracted-output folder")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True, help="Results folder; matching completed results are reused")
    parser.add_argument("--force", action="store_true", help="Run fresh assessments instead of reusing completed ones")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    seen_models = []
    for model in args.models:
        if model in seen_models:
            parser.error("Choose each model once; use --repeats for repeated runs")
        seen_models.append(model)

    try:
        inputs = args.inputs
        if args.input_dir is not None:
            if not args.input_dir.is_dir():
                raise ValueError("Output folder not found: " + str(args.input_dir) + ". Run extract_job.py first.")
            inputs = []
            for path in sorted(args.input_dir.iterdir()):
                if path.is_file() and path.suffix.lower() == ".json":
                    inputs.append(path)
            if not inputs:
                raise ValueError("No extracted JSON files in " + str(args.input_dir) + ". Run extract_job.py first.")
        cases = evaluation_cases(inputs)
        previous = {}
        manifest_path = args.output / "comparison.json"
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                raise ValueError("The saved comparison must contain a JSON object")
    except (OSError, ValueError) as error:
        parser.error(str(error))

    # Make the entire plan from local files BEFORE contacting Ollama.
    plan = []
    pending_models = []
    for model in args.models:
        for repetition in range(1, args.repeats + 1):
            for case in cases:
                saved = None
                if not args.force:
                    saved = find_saved_run(previous, case, model, repetition, args.output)
                plan.append({"case": case, "model": model, "repetition": repetition, "saved": saved})
                if saved is None and model not in pending_models:
                    pending_models.append(model)

    if pending_models:
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        available = Client(host=host).list()
        installed = []
        for item in available.models:
            installed.append(item.model)
        for model in pending_models:
            if model not in installed:
                parser.error("Model not installed: " + model + ". Run ollama pull " + model)
    else:
        print("All requested assessments are already saved. No model or verification calls are needed.", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    should_keep_previous = bool(pending_models) or previous.get("cases") != cases
    if manifest_path.exists() and should_keep_previous:
        # Retain the preceding experiment, including the old automatic synthetic cases.
        backup_number = 1
        backup = args.output / f"previous-comparison-{backup_number:03d}.json"
        while backup.exists():
            backup_number += 1
            backup = args.output / f"previous-comparison-{backup_number:03d}.json"
        backup.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

    experiment = {
        "models": args.models, "repeats": args.repeats, "mode": "live_or_saved",
        "note": "Only supplied postings are included. Saved results retain their original timings; they are not fresh speed measurements. Use --force for fresh evidence, changed prompts/settings or a new timing experiment. Loading and network conditions affect live timings. No real-posting authenticity labels.",
        "cases": cases, "runs": [],
    }
    index = 0
    for planned_run in plan:
        index += 1
        case = planned_run["case"]
        model = planned_run["model"]
        repetition = planned_run["repetition"]
        saved = planned_run["saved"]
        if saved is not None:
            print(f"Skipping {model}: {case['name']} ({repetition}) — saved result", flush=True)
            row = saved
        else:
            print(f"Running {model}: {case['name']} ({repetition})", flush=True)
            row = run_case(case, model, repetition, args.output, index)
            print(f"  {row['status']} in {row['seconds']}s", flush=True)
        experiment["runs"].append(row)
        save_experiment(experiment, args.output)
    print(f"Open {args.output / 'comparison.html'}", flush=True)


if __name__ == "__main__":
    main()
