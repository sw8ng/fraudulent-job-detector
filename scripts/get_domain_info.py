"""Retrieve public domain registration evidence independently of redirect tracing."""

import argparse
import json
from datetime import datetime, timezone

import httpx

from inspect_application_url import domain_for, lookup_domain


def get_domain_info(domain):
    now = datetime.now(timezone.utc)
    result = {"domain": domain, "checked_at": now.isoformat(), "lookup_success": False,
              "domain_created": None, "domain_age_days": None, "registrar": None,
              "domain_status": [], "rdap_source_url": None, "errors": []}
    try:
        url = domain if "://" in domain else "https://" + domain
        result["domain"] = domain_for(url)
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
            result.update(lookup_domain(client, result["domain"], now))
    except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError) as error:
        result["errors"].append({"stage": "rdap", "message": str(error)})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    args = parser.parse_args()
    print(json.dumps(get_domain_info(args.domain), indent=2))
