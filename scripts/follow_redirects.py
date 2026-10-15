"""Trace HTTP redirect destinations without performing RDAP lookup."""

import argparse
import json
from urllib.parse import urljoin, urlsplit

import httpx


def follow_redirects(url, *, client=None, max_redirects=10):
    # Import at call time to share URL validation with the composed inspection tool.
    from inspect_application_url import fetch, domain_for, REDIRECT_CODES

    if client is None:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as connection:
            return follow_redirects(url, client=connection, max_redirects=max_redirects)
    result = {"original_url": url, "redirect_chain": [], "redirects": [],
              "final_url": None, "final_domain": None, "https": None,
              "http_status": None, "redirect_lookup_success": False, "errors": [],
              "limitations": ["Only HTTP redirects are followed; confirmation links and JavaScript navigation are not executed."]}
    try:
        for step in range(max_redirects + 1):
            if url in result["redirect_chain"]:
                raise ValueError("Redirect loop detected")
            status, location = fetch(client, url)
            result["redirect_chain"].append(url)
            result["http_status"] = status
            if status not in REDIRECT_CODES:
                result.update(final_url=url, final_domain=domain_for(url),
                              https=urlsplit(url).scheme == "https", redirect_lookup_success=True)
                if status >= 400:
                    result["errors"].append({"stage": "http", "message": f"Destination returned HTTP {status}; page unavailable"})
                break
            if not location:
                raise ValueError("Redirect has no Location header")
            if step == max_redirects:
                raise ValueError("Application redirect limit reached")
            target = urljoin(url, location)
            result["redirects"].append({"from": url, "to": target, "status": status})
            url = target
    except (httpx.HTTPError, OSError, ValueError, UnicodeError) as error:
        result["errors"].append({"stage": "redirects", "message": str(error)})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    print(json.dumps(follow_redirects(args.url), indent=2))
