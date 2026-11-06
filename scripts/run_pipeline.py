"""Optional shortcut: run the existing extraction and comparison commands in order."""

import argparse
import subprocess
import sys
import webbrowser
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def find_sources(input_dir):
    """Find the saved posting files, without touching any existing outputs."""
    if not input_dir.is_dir():
        raise ValueError("Input folder not found: " + str(input_dir))
    sources = []
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in (".html", ".pdf"):
            sources.append(path)
    if not sources:
        raise ValueError("No HTML or PDF files found in " + str(input_dir))
    return sources


def extracted_paths(sources, output_dir):
    """Compare only outputs corresponding to the current source files, not stale JSON."""
    paths = []
    for source in sources:
        if source.suffix.lower() == ".pdf":
            name = source.name + ".json"
        else:
            name = source.stem + ".json"
        path = output_dir / name
        if not path.is_file():
            raise ValueError("Expected extracted output was not created: " + str(path))
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-model", required=True, help="Model for any missing extraction outputs")
    parser.add_argument("--models", nargs="+", required=True, help="Models to compare on the same extracted outputs")
    parser.add_argument("--input-dir", type=Path, default=PROJECT / "data/input")
    parser.add_argument("--extracted-dir", type=Path, default=PROJECT / "data/output")
    parser.add_argument("--output", type=Path, default=PROJECT / "local/evaluation/actual-postings-comparison")
    parser.add_argument("--force", action="store_true", help="Run fresh assessments; do not re-extract saved outputs")
    parser.add_argument("--force-extraction", action="store_true", help="Also re-extract existing posting outputs")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--no-open", action="store_true", help="Save the visual without opening a browser")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    seen_models = []
    for model in args.models:
        if model in seen_models:
            parser.error("Choose each comparison model once")
        seen_models.append(model)
    try:
        sources = find_sources(args.input_dir)
    except ValueError as error:
        parser.error(str(error))

    extraction = [sys.executable, str(PROJECT / "scripts/extract_job.py"),
                  "--model", args.extract_model, "--input-dir", str(args.input_dir),
                  "--output-dir", str(args.extracted_dir)]
    if args.force_extraction:
        extraction.append("--force")
    print("Step 1: extract missing outputs (existing outputs are kept unless --force-extraction is set).", flush=True)
    result = subprocess.run(extraction)
    if result.returncode != 0:
        print("Extraction failed. Comparison was not started.", file=sys.stderr)
        return result.returncode
    try:
        paths = extracted_paths(sources, args.extracted_dir)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    comparison = [sys.executable, str(PROJECT / "scripts/compare_models.py"), "--models"]
    comparison.extend(args.models)
    comparison.append("--inputs")
    for path in paths:
        comparison.append(str(path))
    comparison.extend(["--output", str(args.output), "--repeats", str(args.repeats)])
    if args.force:
        comparison.append("--force")
    print("Step 2: compare the selected models using the extracted outputs.", flush=True)
    result = subprocess.run(comparison)
    if result.returncode != 0:
        print("Comparison stopped. Check the error above.", file=sys.stderr)
        return result.returncode

    visual = args.output / "comparison.html"
    print("Step 3: performance visual saved at " + str(visual), flush=True)
    if not args.no_open and visual.is_file():
        webbrowser.open(visual.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
