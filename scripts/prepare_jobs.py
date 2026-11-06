import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError


def prepare_job(html_path):
    """Return text and supplied links from a saved HTML or text-based PDF."""
    path = Path(html_path).expanduser()
    if path.suffix.lower() == ".pdf":
        return prepare_pdf(path)
    if path.suffix.lower() != ".html":
        raise ValueError("Choose a saved .html or .pdf file.")

    return prepare_html(path.read_bytes())


def prepare_html(html, base_url=None):
    """Clean saved or downloaded HTML, retaining link destinations beside labels."""
    soup = BeautifulSoup(html, "html.parser")
    # Use the same cleanup for saved postings and downloaded application pages.
    for tag in soup.find_all(["nav", "footer", "script", "style", "template"]):
        tag.decompose()

    # Keep link destinations next to their labels for the LLM.
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if base_url and href and not href.startswith("#"):
            href = urljoin(base_url, href)
        if href.lower().startswith(("http://", "https://", "mailto:", "tel:")):
            link.append(f" ({href})")

    text = soup.get_text(separator="\n", strip=True)
    if not text:
        raise ValueError("The saved HTML file contains no extractable text.")
    return text


def prepare_pdf(path):
    """Extract selectable text; preserve embedded link annotations for URL fields."""
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("The PDF is password protected. Save an unlocked copy.")
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if not text.strip():
                raise ValueError("A PDF page has no extractable text. Image-only PDFs need OCR; save a text-based PDF instead.")
            links = []
            for reference in page.get("/Annots", []):
                annotation = reference.get_object()
                action = annotation.get("/A")
                if action:
                    action = action.get_object()
                    uri = action.get("/URI")
                    if isinstance(uri, str) and uri.lower().startswith(("https://", "http://", "mailto:", "tel:")) and uri not in links:
                        links.append(uri)
            if links:
                text += "\nEmbedded PDF links (page destinations; labels may be unavailable):\n" + "\n".join(links)
            pages.append(text)
        if not pages:
            raise ValueError("The PDF has no pages.")
        return "\n\n".join(pages)
    except PdfReadError as error:
        raise ValueError(f"Unable to read PDF: {error}") from error


if __name__ == "__main__":
    input_dir = Path(__file__).resolve().parent.parent / "data" / "input"
    try:
        input_dir.mkdir(parents=True, exist_ok=True)
        html_files = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in (".html", ".pdf"))
        if not html_files:
            print(f"No HTML or PDF files found in {input_dir}.")
        for html_path in html_files:
            if html_path.is_file():
                print(f"\n--- {html_path.name} ---")
                print(prepare_job(html_path))
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
