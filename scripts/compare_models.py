"""Compare only the supplied postings and reuse matching completed assessments."""

import argparse
import json
import os
import shutil
import time
from copy import deepcopy
from pathlib import Path

from ollama import Client
from bounded_assessment import run_bounded_assessment
from performance_view import write_dashboard
from saved_results import posting_fingerprint, read_report
from quality_evaluation import attach_quality, load_ground_truth
from model_client import provider_for_model


def preflight_claude_model(model):
    from provider_clients import AnthropicClient
    return AnthropicClient(timeout=10).preflight(model)


def preflight_openai_model(model):
    from provider_clients import OpenAIClient
    return OpenAIClient(timeout=10).preflight(model)


def preflight_models(models):
    local = [model for model in models if provider_for_model(model) == 'ollama']
    if local:
        host = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
        available = Client(host=host, timeout=10).list()
        installed = [item.model for item in available.models]
        for model in local:
            if model not in installed:
                raise ValueError('Model not installed: ' + model + '. Run ollama pull ' + model)
    for model in models:
        if provider_for_model(model) == 'anthropic':
            preflight_claude_model(model)
        elif provider_for_model(model) == 'openai':
            preflight_openai_model(model)


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
        if row.get("status") not in ("completed", "timed_out"):
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
    row['conclusion'] = report.get('conclusion')
    if investigation.get('evidence_reuse'):
        row['evidence_reused'] = True
        row['recorded_tool_seconds'] = row['tool_seconds']
        row['tool_seconds'] = 0


def run_case(case, model, repetition, folder, index, max_seconds=300, saved_investigation=None):
    """Run one missing assessment. A failed run is saved as a failure, never as reusable work."""
    calls = []
    row = {
        "case": case["name"], "kind": case["kind"], "source": case["source"],
        "model": model, "repetition": repetition, "status": "failed", "reused": False,
        "raw_concerns": None, "corrected_concerns": None,
    }
    started = time.perf_counter()
    try:
        if saved_investigation is None:
            report = run_bounded_assessment(case["job"], model=model, max_seconds=max_seconds)
        else:
            report = run_bounded_assessment(case['job'], model=model, max_seconds=max_seconds,
                                            saved_investigation=saved_investigation)
        performance = report["investigation"].get("performance", {})
        calls = performance.get("model_calls", [])
        # Keep old report files even when --force requests a fresh measurement.
        filename = f"report-{index:03d}.json"
        while (folder / filename).exists():
            index += 1
            filename = f"report-{index:03d}.json"
        (folder / filename).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        row["report"] = filename
        add_report_metrics(row, report)
        row["status"] = report.get("execution_status", "completed")
        if row["status"] != "completed":
            row["error"] = report.get("stop_reason", "Assessment stopped before completion")
            row["timing_incomplete"] = True
    except Exception as error:
        row["error"] = type(error).__name__ + ": " + str(error)
    row["seconds"] = round(time.perf_counter() - started, 3)
    row["model_calls"] = calls
    row["model_seconds"] = 0
    row["output_tokens"] = 0
    generation_seconds = 0
    for call in calls:
        row["model_seconds"] += call["seconds"]
        row["output_tokens"] += call.get("eval_count", 0)
        generation_seconds += call.get("eval_duration", 0) / 1e9
    row["model_seconds"] = round(row["model_seconds"], 3)
    estimates = [call.get('estimated_api_cost_usd') for call in calls]
    if estimates and all(isinstance(value, (int, float)) for value in estimates):
        row['estimated_api_cost_usd'] = round(sum(estimates), 8)
        row['pricing_note'] = calls[0].get('pricing_note')
    row['provider'] = provider_for_model(model)
    row["tokens_per_second"] = None
    if generation_seconds > 0 and not row.get("timing_incomplete"):
        row["tokens_per_second"] = round(row["output_tokens"] / generation_seconds, 2)
    return row


def save_experiment(experiment, folder):
    """Update the visible results after each case, including failures and reused reports."""
    text = json.dumps(experiment, indent=2, ensure_ascii=False)
    temporary_path = folder / "comparison.json.tmp"
    temporary_path.write_text(text, encoding="utf-8")
    temporary_path.replace(folder / "comparison.json")
    write_dashboard(experiment, folder / "comparison.html")


def evaluate_saved(comparison, ground_truth_dir, output):
    """Create another version of the same comparison, without model/network work."""
    comparison, output = Path(comparison), Path(output)
    if output.exists():
        raise ValueError("Choose a new output folder; existing comparisons are preserved")
    experiment = json.loads(comparison.read_text(encoding="utf-8"))
    truths = load_ground_truth(ground_truth_dir)
    evaluated = attach_quality(experiment, comparison.parent, truths)
    output.mkdir(parents=True)
    for row in experiment["runs"]:
        filename = row.get("report")
        if filename and Path(filename).name == filename:
            source = comparison.parent / filename
            if source.is_file():
                shutil.copy2(source, output / filename)
    evaluated["mode"] = "saved_evaluation"
    evaluated["source_comparison"] = str(comparison.resolve())
    save_experiment(evaluated, output)
    return evaluated


def reassess_comparison(comparison, output, *, models=None, ground_truth_dir=None,
                        max_seconds=300, max_total_seconds=600):
    """Reuse exact per-run tool evidence, measuring only new final generation."""
    started = time.perf_counter()
    if max_seconds <= 0 or max_total_seconds <= 0:
        raise ValueError('Time limits must be positive')
    comparison, output = Path(comparison), Path(output)
    if output.exists():
        raise ValueError('Choose a new output folder; original results are preserved')
    previous = json.loads(comparison.read_text(encoding='utf-8'))
    truths = load_ground_truth(ground_truth_dir) if ground_truth_dir else None
    selected = models or previous['models']
    if any(model not in previous['models'] for model in selected):
        raise ValueError('Replay models must occur in the saved comparison; evidence stays bound to its original run')
    cases = {case['source']: case for case in previous['cases']}
    plan = []
    for old in previous['runs']:
        if old['model'] not in selected:
            continue
        filename = old.get('report')
        if not filename or Path(filename).name != filename:
            raise ValueError('Replay requires a local report filename for every source attempt')
        report = json.loads((comparison.parent / filename).read_text(encoding='utf-8'))
        case = cases[old['source']]
        if report['investigation'].get('input_fingerprint') != posting_fingerprint(case['job']) or report['investigation']['model'] != old['model']:
            raise ValueError('Saved evidence input/model mismatch')
        plan.append((old, case, report))
    experiment = {'models': selected, 'repeats': previous.get('repeats', 1),
        'cases': deepcopy(previous['cases']), 'runs': [], 'mode': 'saved_evidence_reassessment',
        'source_comparison': str(comparison.resolve()),
        'note': 'Final generation only on each original run\'s recorded evidence. No new extraction, search, or verification tools. Timings measure final generation, not full assessment speed. Original incomplete attempts are preserved without retry.',
        'limits': {'assessment_seconds': max_seconds, 'batch_seconds': max_total_seconds}}
    output.mkdir(parents=True)
    for index, (old, case, report) in enumerate(plan, 1):
        if old['status'] != 'completed':
            row = dict(old, reused=True, preserved_incomplete=True)
            row['report'] = f'preserved-{index:03d}.json'
            shutil.copy2(comparison.parent / old['report'], output / row['report'])
        elif max_total_seconds - (time.perf_counter() - started) < max_seconds:
            row = {'case': old['case'], 'source': old['source'], 'model': old['model'],
                   'repetition': old['repetition'], 'status': 'not_run', 'seconds': None,
                   'error': 'Full final-generation allowance does not fit remaining batch budget'}
        else:
            print('Reassessing saved evidence: ' + old['model'] + ': ' + old['case'], flush=True)
            row = run_case(case, old['model'], old['repetition'], output, index,
                           max_seconds=max_seconds, saved_investigation=report['investigation'])
            row['source_report'] = str((comparison.parent / old['report']).resolve())
            print('  ' + row['status'] + ' in ' + str(row['seconds']) + 's', flush=True)
        experiment['runs'].append(row)
        if truths:
            experiment = attach_quality(experiment, output, truths)
        save_experiment(experiment, output)
    return experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", help="Ollama tags and/or Claude/GPT API model IDs")
    input_options = parser.add_mutually_exclusive_group()
    input_options.add_argument("--inputs", nargs="+", type=Path, help="Assess these extracted posting JSON files")
    input_options.add_argument("--input-dir", type=Path, help="Assess every JSON file directly in this extracted-output folder")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True, help="Results folder; matching completed results are reused")
    parser.add_argument("--force", action="store_true", help="Run fresh assessments instead of reusing completed ones")
    parser.add_argument("--max-seconds", type=float, default=300, help="Hard limit per assessment (default: 300 seconds)")
    parser.add_argument("--max-total-seconds", type=float, default=600, help="Stop starting work after this batch budget (default: 600 seconds)")
    parser.add_argument("--ground-truth-dir", type=Path, help="Manually authored quality-evaluation cases")
    parser.add_argument("--evaluate-saved", type=Path, help="Evaluate a saved comparison JSON without models or tools")
    parser.add_argument('--reassess-saved', type=Path, help='Regenerate final assessments using saved per-run evidence; no verification tools')
    args = parser.parse_args()
    if args.reassess_saved:
        if args.evaluate_saved or args.inputs or args.input_dir or args.force:
            parser.error('Evidence replay takes a source comparison, optional models and a new output folder')
        try:
            reassess_comparison(args.reassess_saved, args.output, models=args.models,
                ground_truth_dir=args.ground_truth_dir, max_seconds=args.max_seconds,
                max_total_seconds=args.max_total_seconds)
        except (OSError, ValueError, KeyError) as error:
            parser.error(str(error))
        print(f"Open {args.output / 'comparison.html'}")
        return
    if args.evaluate_saved:
        if not args.ground_truth_dir:
            parser.error("--evaluate-saved requires --ground-truth-dir")
        if args.models or args.inputs or args.input_dir or args.force:
            parser.error("Saved-only evaluation does not accept model-run options")
        try:
            evaluate_saved(args.evaluate_saved, args.ground_truth_dir, args.output)
        except (OSError, ValueError, KeyError) as error:
            parser.error(str(error))
        print(f"Open {args.output / 'comparison.html'}")
        return
    if not args.models or not (args.inputs or args.input_dir):
        parser.error("Supply --models and either --inputs or --input-dir for model runs")
    try:
        truths = load_ground_truth(args.ground_truth_dir) if args.ground_truth_dir else None
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.max_seconds <= 0 or args.max_total_seconds <= 0:
        parser.error("Time limits must be positive")
    batch_started = time.perf_counter()
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
        try:
            preflight_models(pending_models)
        except (ValueError, ImportError) as error:
            parser.error(str(error))
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
        "limits": {"assessment_seconds": args.max_seconds, "batch_seconds": args.max_total_seconds},
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
            remaining = args.max_total_seconds - (time.perf_counter() - batch_started)
            if remaining < args.max_seconds:
                row = {"case": case["name"], "source": case["source"], "kind": case["kind"],
                       "model": model, "repetition": repetition, "status": "not_run", "reused": False,
                       "seconds": None, "model_seconds": None, "model_calls": [],
                       "error": "Insufficient batch time for the full assessment allowance; this case was not started"}
                print(f"Not started: {model}: {case['name']} (full assessment allowance would exceed batch limit)", flush=True)
            else:
                print(f"Running {model}: {case['name']} ({repetition})", flush=True)
                row = run_case(case, model, repetition, args.output, index, max_seconds=args.max_seconds)
                print(f"  {row['status']} in {row['seconds']}s", flush=True)
        experiment["runs"].append(row)
        if truths is not None:
            experiment = attach_quality(experiment, args.output, truths)
        save_experiment(experiment, args.output)
    print(f"Open {args.output / 'comparison.html'}", flush=True)


if __name__ == "__main__":
    main()
