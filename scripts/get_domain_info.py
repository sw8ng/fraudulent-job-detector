"""Investigate a company website domain and its public registration evidence."""

import argparse
import json
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from inspect_application_url import domain_for, validate_public_url, REDIRECT_CODES


def lookup_domain(client, domain, now):
    url = f"https://rdap.org/domain/{domain}"
    for _ in range(6):
        # Bootstrap providers redirect to the authoritative registry.
        validate_public_url(url)
        with client.stream("GET", url) as response:
            if response.status_code in REDIRECT_CODES:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("RDAP redirect has no Location header")
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ValueError("RDAP response exceeds size limit")
            record = json.loads(body)
            break
    else:
        raise ValueError("RDAP redirect limit reached")
    created = next((e.get("eventDate") for e in record.get("events", [])
                    if e.get("eventAction") == "registration"), None)
    age = None
    if created:
        date = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        age = (now - date).days
        if age < 0:
            raise ValueError("Registry registration date is in the future")
    registrar = None
    for entity in record.get("entities", []):
        if "registrar" in entity.get("roles", []):
            card = entity.get("vcardArray", [])
            if len(card) == 2:
                registrar = next((p[3] for p in card[1] if len(p) > 3 and p[0] == "fn"), None)
            break
    return {"domain_created": created, "domain_age_days": age,
            "registrar": registrar, "domain_status": record.get("status", []),
            "rdap_source_url": url, "lookup_success": True}


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
