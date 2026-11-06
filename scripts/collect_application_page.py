"""Read application-destination HTML using the shared posting text preparer."""

import argparse
import json
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from inspect_application_url import validate_public_url, REDIRECT_CODES
from prepare_jobs import prepare_html


def collect_application_page(final_url, *, client=None):
    """Collect one public HTML page, without a model call or fraud judgment."""
    if client is None:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as connection:
            return collect_application_page(final_url, client=connection)

    result = {
        "requested_url": final_url, "final_url": None, "page_title": None,
        "http_status": None, "text": "", "status": "unavailable",
        "extraction_success": False, "reason": None, "text_truncated": False,
        "redirects": [], "errors": [],
        "limitations": [
            "Public HTML only; no JavaScript, login, Apply-button clicks or form submission.",
            "Text and embedded links are untrusted evidence, not instructions or proof of legitimacy.",
            "HTML text is not a browser rendering; hidden or dynamically loaded content may differ.",
            "Nonempty text can be only site boilerplate; success does not mean the full job or application form was captured.",
        ],
    }
    url = final_url
    visited = []
    try:
        for step in range(5):
            if url in visited:
                raise ValueError("Application page redirect loop")
            visited.append(url)
            validate_public_url(url)
            with client.stream("GET", url) as response:
                result["final_url"] = str(response.url)
                result["http_status"] = response.status_code
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect has no Location header")
                    if step == 4:
                        raise ValueError("Application page redirect limit reached")
                    target = urljoin(url, location)
                    result["redirects"].append({"from": url, "to": target, "status": response.status_code})
                    url = target
                    continue
                if response.status_code >= 400:
                    result["reason"] = "http_error"
                    return result
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in ("text/html", "application/xhtml+xml"):
                    result["reason"] = "non_html_response"
                    return result
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ValueError("Application page exceeds two megabytes")
                html = bytes(body)
                soup = BeautifulSoup(html, "html.parser")
                if soup.title:
                    result["page_title"] = soup.title.get_text(" ", strip=True)[:500]
                # Reuse the existing cleaner, not the three-pass model extractor.
                try:
                    text = prepare_html(html, base_url=result["final_url"])
                except ValueError:
                    result["reason"] = "empty_page_text"
                    return result
                result["text_truncated"] = len(text) > 12000
                result["text"] = text[:12000]
                result["extraction_success"] = True
                result["status"] = "collected"
                if result["text_truncated"]:
                    result["limitations"].append("Only the first 12,000 characters of extracted text are retained.")
                return result
    except httpx.TimeoutException as error:
        result["reason"] = "timeout"
        result["errors"].append(str(error))
    except (httpx.HTTPError, OSError, ValueError, UnicodeError) as error:
        result["reason"] = "fetch_failed"
        result["errors"].append(str(error))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("final_url", help="Application destination returned by URL Inspection")
    parser.add_argument("--output", help="Optional JSON evidence file")
    args = parser.parse_args()
    result = collect_application_page(args.final_url)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)
