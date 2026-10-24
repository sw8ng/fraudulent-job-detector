"""Compare posting email domains with a candidate website; never verify identity."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from inspect_application_url import domain_for


EMAIL = re.compile(r"(?<![\w.+%-])([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9\u0080-\uffff-]+\.)+[A-Za-z\u0080-\uffff]{2,})(?![\w@-])")
FREE_PROVIDERS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
                  "live.com", "yahoo.com", "ymail.com", "aol.com", "icloud.com",
                  "proton.me", "protonmail.com", "mail.com", "gmx.com"}


def posting_emails(job):
    """Keep source snippets, and reject extracted contacts absent from raw evidence."""
    records = []
    seen = set()
    raw = job.get("raw_posting_text")
    text_field = "description" if job.get("description") else "raw_posting_text"
    for field in ("contact_email", text_field):
        source = job.get(field) or ""
        for match in EMAIL.finditer(source):
            address = match.group(1)
            if raw and address.casefold() not in raw.casefold():
                continue
            if address.casefold() in seen:
                continue
            seen.add(address.casefold())
            # Prefer original surrounding text over an isolated extracted field.
            original_match = re.search(re.escape(address), raw, re.I) if raw else None
            quote_source = raw if original_match else source
            quote_match = original_match or match
            records.append({"email": address, "source_field": field,
                            "quote": quote_source[max(0, quote_match.start() - 80):quote_match.end() + 80],
                            "quote_source": "raw_posting_text" if original_match else field,
                            "present_in_raw_posting": bool(raw)})
    return records


def check_recruiter_email(job, company_website=None):
    """No network or model calls; addresses are mentions, not authenticated senders."""
    result = {"status": "checked", "company_website": company_website,
              "company_domain": None, "verified": False, "emails": [],
              "limitations": [
                  "Candidate website ownership and recruiter affiliation remain unverified.",
                  "Email mentions may belong to privacy, accessibility or other contacts, not a recruiter.",
                  "No mailbox ownership, sender authentication, DNS or email delivery is checked.",
                  "Different domains may belong to staffing agencies; free email alone is not fraud evidence.",
                  "The free-provider list is limited; other providers may be unrecognized."
              ]}
    if company_website:
        try:
            parsed = urlsplit(company_website)
            if parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
                raise ValueError("Expected an HTTP/HTTPS candidate website without credentials")
            result["company_domain"] = domain_for(company_website)
        except ValueError as error:
            result["limitations"].append("Company domain could not be compared: " + str(error))
    for record in posting_emails(job):
        try:
            host = record["email"].rsplit("@", 1)[1].encode("idna").decode("ascii").lower()
            record["email_host"] = host
            domain = domain_for("https://" + host)
        except (ValueError, UnicodeError):
            record.update(email_domain=None, comparison="invalid_domain", free_provider=False)
        else:
            free = host in FREE_PROVIDERS
            if free:
                comparison = "free_provider"
            elif not result["company_domain"]:
                comparison = "no_company_domain"
            elif domain == result["company_domain"]:
                comparison = "matching_domain"
            else:
                comparison = "different_domain"
            record.update(email_domain=domain, comparison=comparison, free_provider=free)
        result["emails"].append(record)
    if not result["emails"]:
        result["status"] = "no_email_available"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare posting email domains without sending email")
    parser.add_argument("job_json", type=Path)
    parser.add_argument("--company-website", help="Override the candidate website for comparison")
    args = parser.parse_args()
    job = json.loads(args.job_json.read_text(encoding="utf-8"))
    print(json.dumps(check_recruiter_email(job, args.company_website or job.get("company_website")), indent=2, ensure_ascii=False))
