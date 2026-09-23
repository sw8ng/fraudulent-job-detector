import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup


def prepare_job(html_path):
    """Return text from a locally saved HTML file, from any website."""
    path = Path(html_path).expanduser()
    if path.suffix.lower() != ".html":
        raise ValueError("Choose a saved .html file.")

    # Parse bytes so BeautifulSoup can detect the saved page's character encoding.
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    if not text:
        raise ValueError("The saved HTML file contains no extractable text.")
    return text


if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent.parent
    input_dir = project_dir / "data" / "input"
    output_dir = project_dir / "data" / "output"
    try:
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        html_files = sorted(
            path for path in input_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".html"
        )
    except OSError as error:
        print(f"Error preparing folders: {error}", file=sys.stderr)
        sys.exit(1)

    if not html_files:
        print(f"No HTML files found. Save .html files in {input_dir} and run again.")

    failed = False
    for html_path in html_files:
        try:
            text = prepare_job(html_path)
            job = {
                "source_file": html_path.name,
                "raw_posting_text": text,
            }
            output_path = output_dir / (html_path.name + ".json")
            output_path.write_text(
                json.dumps(job, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"Saved {output_path.name}.")
        except (OSError, ValueError) as error:
            failed = True
            print(f"Error processing {html_path.name}: {error}", file=sys.stderr)

    sys.exit(1 if failed else 0)
