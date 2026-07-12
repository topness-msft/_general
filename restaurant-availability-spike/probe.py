import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BLOCKED = "BLOCKED"
RATE_LIMITED = "RATE_LIMITED"
SLOTS_FOUND = "SLOTS_FOUND"
NO_SLOTS = "NO_SLOTS"
UPSTREAM_CHANGED = "UPSTREAM_CHANGED"
HTTP_ERROR = "HTTP_ERROR"
NETWORK_ERROR = "NETWORK_ERROR"

ORIGIN = "https://www.opentable.com"
BOOTSTRAP_PATH = "/restaurant/profile/100"
GQL_PATH = "/dapi/fe/gql"
DEFAULT_QUERY_HASH = "436770d3236803f6bb7e8bdfc7b617a582026235c1a6af52297ab63fed08aa0c"
CHALLENGE_MARKERS = (
    "access denied",
    "just a moment",
    "captcha",
    "_cf_chl",
    "akamai bot manager",
)


def extract_csrf(html: str) -> str | None:
    match = re.search(
        r'(?:window\.__CSRF_TOKEN__\s*=\s*[\'"]|"__CSRF_TOKEN__"\s*:\s*")'
        r"([0-9a-fA-F-]{16,})",
        html,
    )
    return match.group(1) if match else None


def _availability_shape(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    data = body.get("data")
    if not isinstance(data, dict):
        return False
    availability = data.get("availability")
    if not isinstance(availability, list) or not availability:
        return False
    first = availability[0]
    if not isinstance(first, dict):
        return False
    days = first.get("availabilityDays")
    if not isinstance(days, list) or not days:
        return False
    return isinstance(days[0], dict) and isinstance(days[0].get("slots"), list)


def classify_response(status_code: int, body: str) -> str:
    lowered = body.lower()
    if status_code in (401, 403) or any(marker in lowered for marker in CHALLENGE_MARKERS):
        return BLOCKED
    if status_code == 429:
        return RATE_LIMITED
    if status_code >= 500:
        return HTTP_ERROR

    try:
        parsed = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return UPSTREAM_CHANGED

    errors = parsed.get("errors") if isinstance(parsed, dict) else None
    if errors:
        return UPSTREAM_CHANGED
    if status_code != 200 or not _availability_shape(parsed):
        return UPSTREAM_CHANGED
    return SLOTS_FOUND if extract_slots(parsed) else NO_SLOTS


def _offset_time(anchor_time: str, offset_minutes: int) -> str:
    hour, minute = (int(value) for value in anchor_time.split(":", maxsplit=1))
    total = (hour * 60 + minute + offset_minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def extract_slots(body: Any, anchor_time: str = "19:00") -> list[dict[str, str]]:
    try:
        availability = body["data"]["availability"]
        if not isinstance(availability, list) or not availability:
            return []
        days = availability[0]["availabilityDays"]
        if not isinstance(days, list) or not days:
            return []
        slots = days[0]["slots"]
        if not isinstance(slots, list):
            return []
    except (KeyError, TypeError, IndexError):
        return []

    output: list[dict[str, str]] = []
    for slot in slots:
        if not isinstance(slot, dict) or slot.get("isAvailable") is False:
            continue
        slot_hash = slot.get("slotHash")
        if not isinstance(slot_hash, str) or not slot_hash:
            continue
        try:
            offset = int(slot.get("timeOffsetMinutes", 0))
        except (TypeError, ValueError):
            continue
        item = {
            "time": _offset_time(anchor_time, offset),
            "slot_hash": slot_hash,
        }
        slot_type = slot.get("type")
        if isinstance(slot_type, str) and slot_type:
            item["type"] = slot_type
        output.append(item)
    return output


def build_availability_payload(
    restaurant_id: int,
    date: str,
    party_size: int,
    anchor_time: str,
    query_hash: str,
    correlation_id: str,
) -> dict[str, Any]:
    return {
        "operationName": "RestaurantsAvailability",
        "variables": {
            "onlyPop": False,
            "forwardDays": 0,
            "requireTimes": False,
            "requireTypes": ["Standard"],
            "useCBR": False,
            "privilegedAccess": ["UberOneDiningProgram"],
            "restaurantIds": [restaurant_id],
            "date": date,
            "time": anchor_time,
            "partySize": party_size,
            "databaseRegion": "NA",
            "restaurantAvailabilityTokens": [],
            "loyaltyRedemptionTiers": [],
            "attributionToken": "",
            "correlationId": correlation_id,
            "forwardMinutes": 150,
            "backwardMinutes": 150,
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": query_hash,
            }
        },
    }


def _bootstrap_headers() -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{ORIGIN}/",
    }


def _gql_headers(csrf: str) -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}{BOOTSTRAP_PATH}",
        "X-CSRF-Token": csrf,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "apollographql-client-name": "fe-search",
        "apollographql-client-version": "0.0.1",
        "ot-page-group": "rest-profile",
        "ot-page-type": "restprofilepage",
        "x-query-timeout": "10000",
    }


def run_probe(
    fixtures: list[dict[str, Any]],
    probe_date: str,
    party_size: int,
    anchor_time: str,
    query_hash: str,
) -> tuple[dict[str, Any], int]:
    from curl_cffi import requests

    started = time.perf_counter()
    session = requests.Session(impersonate="chrome")
    region = os.getenv("FLY_REGION", "local")
    output: dict[str, Any] = {
        "probe_ts": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "date": probe_date,
        "party_size": party_size,
        "anchor_time": anchor_time,
        "query_hash_prefix": query_hash[:16],
        "results": [],
    }

    try:
        bootstrap = session.get(
            f"{ORIGIN}{BOOTSTRAP_PATH}",
            headers=_bootstrap_headers(),
            timeout=30,
        )
    except Exception as error:
        output["bootstrap"] = {"status": NETWORK_ERROR, "error": str(error)}
        output["duration_ms"] = round((time.perf_counter() - started) * 1000)
        return output, 1

    csrf = extract_csrf(bootstrap.text)
    bootstrap_state = (
        "OK"
        if bootstrap.status_code == 200 and csrf
        else BLOCKED
        if bootstrap.status_code in (401, 403)
        or any(marker in bootstrap.text.lower() for marker in CHALLENGE_MARKERS)
        else UPSTREAM_CHANGED
    )
    output["bootstrap"] = {
        "http_status": bootstrap.status_code,
        "status": bootstrap_state,
        "csrf_found": bool(csrf),
    }
    if bootstrap_state != "OK" or not csrf:
        output["duration_ms"] = round((time.perf_counter() - started) * 1000)
        return output, 1 if bootstrap_state == BLOCKED else 2

    gql_url = f"{ORIGIN}{GQL_PATH}?optype=query&opname=RestaurantsAvailability"
    for fixture in fixtures:
        restaurant_id = int(fixture["restaurant_id"])
        payload = build_availability_payload(
            restaurant_id=restaurant_id,
            date=probe_date,
            party_size=party_size,
            anchor_time=anchor_time,
            query_hash=query_hash,
            correlation_id=str(uuid.uuid4()),
        )
        request_started = time.perf_counter()
        response = None
        error_message = None
        for attempt, delay in enumerate((0, 0.75, 5), start=1):
            if delay:
                time.sleep(delay)
            try:
                response = session.post(
                    gql_url,
                    headers=_gql_headers(csrf),
                    json=payload,
                    timeout=30,
                )
            except Exception as error:
                error_message = str(error)
                break
            if response.status_code != 403 or attempt == 3:
                break

        result: dict[str, Any] = {
            "label": fixture.get("label", str(restaurant_id)),
            "restaurant_id": restaurant_id,
            "duration_ms": round((time.perf_counter() - request_started) * 1000),
        }
        if error_message or response is None:
            result.update({"status": NETWORK_ERROR, "error": error_message})
        else:
            state = classify_response(response.status_code, response.text)
            result.update(
                {
                    "status": state,
                    "http_status": response.status_code,
                    "slots": extract_slots(
                        json.loads(response.text),
                        anchor_time=anchor_time,
                    )
                    if state == SLOTS_FOUND
                    else [],
                    "body_preview": response.text[:200]
                    if state not in (SLOTS_FOUND, NO_SLOTS)
                    else None,
                }
            )
        output["results"].append(result)

    valid = sum(
        result["status"] in (SLOTS_FOUND, NO_SLOTS)
        for result in output["results"]
    )
    required_valid = min(3, len(fixtures))
    output["valid_results"] = valid
    output["required_valid_results"] = required_valid
    output["duration_ms"] = round((time.perf_counter() - started) * 1000)
    return output, 0 if valid >= required_valid else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe anonymous OpenTable availability.")
    parser.add_argument("--fixtures", default="fixtures.json")
    parser.add_argument("--date")
    parser.add_argument("--days-ahead", type=int, default=7)
    parser.add_argument("--party-size", type=int, default=2)
    parser.add_argument("--time", default="19:00")
    args = parser.parse_args()

    probe_date = args.date or (date.today() + timedelta(days=args.days_ahead)).isoformat()
    fixtures_path = Path(args.fixtures)
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    output, exit_code = run_probe(
        fixtures=fixtures,
        probe_date=probe_date,
        party_size=args.party_size,
        anchor_time=args.time,
        query_hash=os.getenv("OPENTABLE_AVAILABILITY_HASH", DEFAULT_QUERY_HASH),
    )
    print(json.dumps(output, separators=(",", ":"), ensure_ascii=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
