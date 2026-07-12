import unittest

from probe import (
    NO_RESULTS,
    RESULTS_FOUND,
    UPSTREAM_CHANGED,
    build_autocomplete_payload,
    classify_search_response,
    extract_opentable_results,
    extract_resy_results,
)


class SearchParserTests(unittest.TestCase):
    def test_extracts_and_filters_resy_results_by_city(self):
        body = {
            "search": {
                "hits": [
                    {
                        "id": {"resy": 1387},
                        "name": "Le Bernardin",
                        "locality": "New York",
                        "neighborhood": "Theater District",
                        "_geoloc": {"lat": 40.76, "lng": -73.98},
                        "contact": {"phone_number": "+12125541515"},
                    },
                    {
                        "id": {"resy": 9},
                        "name": "Other",
                        "locality": "Boston",
                    },
                ]
            }
        }
        self.assertEqual(
            extract_resy_results(body, city="New York"),
            [
                {
                    "provider_id": "resy:1387",
                    "name": "Le Bernardin",
                    "locality": "New York",
                    "neighborhood": "Theater District",
                    "latitude": 40.76,
                    "longitude": -73.98,
                    "phone": "+12125541515",
                }
            ],
        )

    def test_extracts_restaurant_only_opentable_results(self):
        body = {
            "data": {
                "autocomplete": {
                    "autocompleteResults": [
                        {
                            "id": "80437",
                            "type": "Restaurant",
                            "name": "RPM Italian - Chicago",
                            "metroName": "Chicago / Illinois",
                            "neighborhoodName": "River North",
                            "latitude": 41.89,
                            "longitude": -87.63,
                        },
                        {"id": "3", "type": "Cuisine", "name": "Italian"},
                    ]
                }
            }
        }
        self.assertEqual(
            extract_opentable_results(body),
            [
                {
                    "provider_id": "ot:80437",
                    "name": "RPM Italian - Chicago",
                    "locality": "Chicago / Illinois",
                    "neighborhood": "River North",
                    "latitude": 41.89,
                    "longitude": -87.63,
                    "phone": None,
                }
            ],
        )

    def test_search_classification_distinguishes_empty_from_bad_shape(self):
        self.assertEqual(
            classify_search_response(200, '{"search":{"hits":[]}}', "resy"),
            NO_RESULTS,
        )
        self.assertEqual(
            classify_search_response(
                200,
                '{"data":{"autocomplete":{"autocompleteResults":[]}}}',
                "opentable",
            ),
            NO_RESULTS,
        )
        self.assertEqual(
            classify_search_response(200, '{"data":{}}', "opentable"),
            UPSTREAM_CHANGED,
        )
        self.assertEqual(
            classify_search_response(
                200,
                '{"search":{"hits":[{"id":{"resy":1387},"name":"Le Bernardin"}]}}',
                "resy",
            ),
            RESULTS_FOUND,
        )

    def test_autocomplete_payload_contains_location_context(self):
        payload = build_autocomplete_payload(
            term="RPM Italian",
            latitude=41.8781,
            longitude=-87.6298,
            query_hash="hash",
        )
        self.assertEqual(payload["operationName"], "Autocomplete")
        self.assertEqual(payload["variables"]["term"], "RPM Italian")
        self.assertEqual(payload["variables"]["latitude"], 41.8781)
        self.assertEqual(payload["variables"]["longitude"], -87.6298)
        self.assertTrue(payload["variables"]["useNewVersion"])


if __name__ == "__main__":
    unittest.main()
