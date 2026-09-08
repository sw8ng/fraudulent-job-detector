import json
import os
import sys
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


def identify_website(url):
    """Identify the website using the URL's hostname."""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Enter a complete URL starting with https://.")

    for website in ("indeed", "ziprecruiter"):
        domain = website + ".com"
        if parsed.hostname == domain or parsed.hostname.endswith("." + domain):
            return website

    raise ValueError("Use an Indeed or ZipRecruiter job URL.")


JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "source_url": {"type": ["string", "null"]},
        "source_website": {"type": ["string", "null"]},
        "job_title": {"type": ["string", "null"]},
        "company": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "remote_type": {"type": ["string", "null"]},
        "employment_type": {"type": ["string", "null"]},
        "salary_min": {"type": ["number", "null"]},
        "salary_max": {"type": ["number", "null"]},
        "salary_currency": {"type": ["string", "null"]},
        "salary_period": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "responsibilities": {"type": ["string", "null"]},
        "qualifications": {"type": ["string", "null"]},
        "benefits": {"type": ["string", "null"]},
        "date_posted": {"type": ["string", "null"]},
        "job_id": {"type": ["string", "null"]},
        "recruiter_name": {"type": ["string", "null"]},
        "contact_email": {"type": ["string", "null"]},
        "contact_phone": {"type": ["string", "null"]},
        "application_method": {"type": ["string", "null"]},
        "external_urls": {
            "type": "array",
            "items": {"type": "string"},
        },
        "communication_platforms": {
            "type": "array",
            "items": {"type": "string"},
        },
        "company_description": {"type": ["string", "null"]},
    },
}


def extract_job(job_url, api_key=None):
    """Return structured information extracted from one job posting."""
    website = identify_website(job_url)
    print(f"Website: {website}", file=sys.stderr)

    api_key = api_key or os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise ValueError("A Firecrawl API key is required for this REST API request.")

    # Send the URL as JSON
    response = requests.post(
        "https://api.firecrawl.dev/v2/scrape",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "url": job_url,
            "formats": [
                "markdown",
                {
                    "type": "json",
                    "schema": JOB_SCHEMA,
                    "prompt": (
                        "Extract information only if it is explicitly present on the job posting. "
                        "Do not guess missing information. "
                        "Use null for missing text or number fields. "
                        "The description should contain the actual job description, not a summary. "
                        "The external_urls field should contain URLs associated with the job posting."
                    ),
                },
            ],
        },
        timeout=60,
    )
    if response.status_code >= 400:
        message = response.text.strip().replace(api_key, "<REDACTED>")
        raise ValueError(f"Firecrawl HTTP {response.status_code}: {message}")

    # Firecrawl wraps the page content in a JSON response
    result = response.json()
    if not result.get("success"):
        message = str(result.get("error", "Scrape failed.")).replace(
            api_key, "<REDACTED>"
        )
        raise ValueError(f"Firecrawl: {message}")
    data = result.get("data") or {}
    content = data.get("markdown") or ""
    metadata = data.get("metadata") or {}
    title = str(metadata.get("title") or "").lower()
    if metadata.get("statusCode", 200) >= 400:
        raise ValueError(f"The job website returned HTTP {metadata['statusCode']}.")
    if not content.strip():
        raise ValueError("Firecrawl returned no page content.")
    if (
        "just a moment" in title
        or "# additional verification required" in content.lower()
    ):
        raise ValueError(
            f"Firecrawl returned a verification page from {website}, not the job posting. "
            "The job content is unavailable through this request."
        )
    job = data.get("json") or {}
    if not isinstance(job, dict):
        raise ValueError("Firecrawl returned job data in an unexpected format.")

    fields_with_null_defaults = (
        "job_title",
        "company",
        "location",
        "remote_type",
        "employment_type",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "description",
        "responsibilities",
        "qualifications",
        "benefits",
        "date_posted",
        "job_id",
        "recruiter_name",
        "contact_email",
        "contact_phone",
        "application_method",
        "company_description",
    )
    for field in fields_with_null_defaults:
        job.setdefault(field, None)

    job.setdefault("external_urls", [])
    job.setdefault("communication_platforms", [])
    job["source_url"] = job_url
    if website == "indeed":
        job["source_website"] = "Indeed"
    else:
        job["source_website"] = "ZipRecruiter"

    return job


if __name__ == "__main__":
    job_url = input("Paste one Indeed or ZipRecruiter job URL: ").strip()
    try:
        api_key = (
            os.environ.get("FIRECRAWL_API_KEY")
            or getpass("Firecrawl API key (hidden): ").strip()
        )
        job = extract_job(job_url, api_key)
        output_path = Path(__file__).with_name("extracted_job.json")
        with output_path.open("w", encoding="utf-8") as output_file:
            json.dump(job, output_file, indent=2, ensure_ascii=False)
        print(f"Saved extracted job information to {output_path}.")
    except (OSError, requests.RequestException, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
