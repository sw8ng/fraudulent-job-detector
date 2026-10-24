"""Gather URL evidence without deciding whether a job is fraudulent."""

import argparse
import ipaddress
import json
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import tldextract


# Use the bundled public suffix list: no extra network requests or disk cache.
EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
REDIRECT_CODES = {301, 302, 303, 307, 308}


def domain_for(url):
    """Return the registrable domain, including multi-label public suffixes."""
    host = urlsplit(url).hostname
    if not host:
        raise ValueError("URL has no hostname")
    host = host.rstrip(".").encode("idna").decode("ascii").lower()
    parts = EXTRACT(host)
    if not parts.suffix or not parts.domain:
        raise ValueError("URL must have a publicly registrable domain")
    return parts.top_domain_under_public_suffix


def validate_public_url(url):
    """Reject credentials, unsupported protocols and non-public destinations."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only absolute HTTP/HTTPS URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not supported")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Destination must resolve only to public IP addresses")


def fetch(client, url):
    # Validate every redirect before visiting it; never submit application forms.
    validate_public_url(url)
    with client.stream("GET", url) as response:
        return response.status_code, response.headers.get("location")


def inspect_application_url(application_url: str, company_website: str | None = None):
    """Inspect an application URL for redirects, destination, HTTPS and domain comparison.

    Args:
        application_url: Exact application URL supplied in the job posting.
        company_website: Claimed employer website, if supplied in the posting.
    """
    now = datetime.now(timezone.utc)
    result = {
        "checked_at": now.isoformat(), "original_url": application_url,
        "redirect_chain": [], "redirects": [], "final_url": None,
        "final_domain": None, "company_domain": None,
        "domain_matches_company": None, "https": None,
        "http_status": None, "redirect_lookup_success": False,
        "errors": [],
    }
    if company_website:
        try:
            result["company_domain"] = domain_for(company_website)
        except (ValueError, UnicodeError) as error:
            result["errors"].append({"stage": "company_domain", "message": str(error)})
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False,
                      headers={"User-Agent": "JobEvidenceResearch/1.0"}) as client:
        from follow_redirects import follow_redirects
        trace = follow_redirects(application_url, client=client)
        result["errors"].extend(trace.pop("errors"))
        result.update(trace)
        if result["final_domain"]:
            if result["company_domain"]:
                result["domain_matches_company"] = result["final_domain"] == result["company_domain"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application_url")
    parser.add_argument("--company-website")
    args = parser.parse_args()
    print(json.dumps(inspect_application_url(args.application_url, args.company_website), indent=2))
