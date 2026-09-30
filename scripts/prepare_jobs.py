import sys
from pathlib import Path

from bs4 import BeautifulSoup


def prepare_job(html_path):
    """Return text from a locally saved HTML file, from any website."""
    path = Path(html_path).expanduser()
    if path.suffix.lower() != ".html":
        raise ValueError("Choose a saved .html file.")

    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    # Remove page navigation and footer boilerplate before sending text to Qwen.
    for tag in soup.find_all(["nav", "footer", "script", "style", "template"]):
        tag.decompose()

    # Keep link destinations next to their labels for the LLM.
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if href.lower().startswith(("http://", "https://", "mailto:", "tel:")):
            link.append(f" ({href})")

    text = soup.get_text(separator="\n", strip=True)
    if not text:
        raise ValueError("The saved HTML file contains no extractable text.")
    return text


if __name__ == "__main__":
    input_dir = Path(__file__).resolve().parent.parent / "data" / "input"
    try:
        input_dir.mkdir(parents=True, exist_ok=True)
        html_files = sorted(input_dir.glob("*.html"))
        if not html_files:
            print(f"No HTML files found in {input_dir}.")
        for html_path in html_files:
            if html_path.is_file():
                print(f"\n--- {html_path.name} ---")
                print(prepare_job(html_path))
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
