import json
import unittest
from pathlib import Path

from probe import (
    BLOCKED,
    HTTP_ERROR,
    NO_SLOTS,
    RATE_LIMITED,
    SLOTS_FOUND,
    UPSTREAM_CHANGED,
    _gql_headers,
    build_availability_payload,
    classify_response,
    extract_csrf,
    extract_slots,
)


FIXTURES = Path(__file__).parent / "fixtures"


class ClassifyResponseTests(unittest.TestCase):
    def test_hard_and_soft_blocks(self):
        self.assertEqual(classify_response(403, "Access Denied"), BLOCKED)
        self.assertEqual(
            classify_response(200, "<html><title>Just a moment...</title>captcha</html>"),
            BLOCKED,
        )
        self.assertEqual(classify_response(200, "<script>window._cf_chl_opt={}</script>"), BLOCKED)

    def test_rate_limit_and_server_error(self):
        self.assertEqual(classify_response(429, "Too Many Requests"), RATE_LIMITED)
        self.assertEqual(classify_response(503, "unavailable"), HTTP_ERROR)

    def test_valid_slot_states(self):
        slots = (FIXTURES / "slots_found.json").read_text(encoding="utf-8")
        empty = (FIXTURES / "no_slots.json").read_text(encoding="utf-8")
        self.assertEqual(classify_response(200, slots), SLOTS_FOUND)
        self.assertEqual(classify_response(200, empty), NO_SLOTS)

    def test_schema_and_persisted_query_failures(self):
        changed = (FIXTURES / "upstream_changed.json").read_text(encoding="utf-8")
        persisted = json.dumps(
            {
                "errors": [
                    {
                        "message": "PersistedQueryNotFound",
                        "extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"},
                    }
                ]
            }
        )
        self.assertEqual(classify_response(200, changed), UPSTREAM_CHANGED)
        self.assertEqual(classify_response(400, persisted), UPSTREAM_CHANGED)
        self.assertEqual(classify_response(200, ""), UPSTREAM_CHANGED)
        self.assertEqual(classify_response(200, "not json"), UPSTREAM_CHANGED)


class ExtractSlotsTests(unittest.TestCase):
    def test_extracts_available_slots_and_resolves_offsets(self):
        body = json.loads((FIXTURES / "slots_found.json").read_text(encoding="utf-8"))
        self.assertEqual(
            extract_slots(body, anchor_time="19:00"),
            [
                {
                    "time": "19:00",
                    "slot_hash": "hash-a",
                    "type": "Standard",
                },
                {
                    "time": "19:30",
                    "slot_hash": "hash-b",
                    "type": "Bar",
                },
            ],
        )

    def test_filters_unavailable_and_tolerates_bad_shapes(self):
        self.assertEqual(extract_slots({}), [])
        self.assertEqual(extract_slots({"data": {"availability": None}}), [])
        self.assertEqual(
            extract_slots(
                {
                    "data": {
                        "availability": [
                            {
                                "availabilityDays": [
                                    {
                                        "slots": [
                                            {
                                                "isAvailable": False,
                                                "timeOffsetMinutes": 0,
                                                "slotHash": "closed",
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            ),
            [],
        )


class RequestContractTests(unittest.TestCase):
    def test_extracts_csrf_from_bootstrap_html(self):
        html = '<script>{"__CSRF_TOKEN__":"12345678-abcd-4abc-8abc-123456789012"}</script>'
        self.assertEqual(extract_csrf(html), "12345678-abcd-4abc-8abc-123456789012")
        self.assertIsNone(extract_csrf("<html>missing</html>"))

    def test_payload_matches_current_consumer_contract(self):
        payload = build_availability_payload(
            restaurant_id=100,
            date="2026-07-19",
            party_size=2,
            anchor_time="19:00",
            query_hash="abc123",
            correlation_id="00000000-0000-4000-8000-000000000001",
        )
        self.assertEqual(payload["operationName"], "RestaurantsAvailability")
        variables = payload["variables"]
        self.assertEqual(variables["restaurantIds"], [100])
        self.assertEqual(variables["databaseRegion"], "NA")
        self.assertEqual(variables["forwardDays"], 0)
        self.assertEqual(variables["forwardMinutes"], 150)
        self.assertEqual(variables["backwardMinutes"], 150)
        self.assertEqual(
            payload["extensions"]["persistedQuery"]["sha256Hash"],
            "abc123",
        )

    def test_graphql_headers_are_same_origin_xhr_context(self):
        headers = _gql_headers("csrf-token")
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")
        self.assertEqual(headers["ot-page-group"], "rest-profile")
        self.assertEqual(headers["ot-page-type"], "restprofilepage")


if __name__ == "__main__":
    unittest.main()
