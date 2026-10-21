"""Contract test for the frontend sample-mode fallback.

The embedded SAMPLE object must always satisfy the shape the render function
consumes, the sample view must stay unmistakably labelled as illustrative, and
only service-side error codes may trigger it. Parsing index.html keeps this test
honest: it exercises the exact bytes that ship.
"""
import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")

# Error codes the backend returns for SERVICE-side failures (plan.py).
SERVICE_SIDE = {"qloo_key_not_configured", "upstream_auth", "upstream_rate_limited", "upstream_error"}
# Client-side plan errors that must keep the plain error card (planner.py PlanError codes).
CLIENT_SIDE = {"bad_input", "bad_request", "bad_json", "no_tastes_resolved", "no_destinations",
               "method_not_allowed"}


def extract_sample():
    match = re.search(r"var SAMPLE = (\{.*?\n  \});", INDEX, re.DOTALL)
    if not match:
        raise AssertionError("SAMPLE object not found in index.html")
    return json.loads(match.group(1))


class SampleShapeTest(unittest.TestCase):
    def setUp(self):
        self.sample = extract_sample()

    def test_destination_shape(self):
        dest = self.sample["destination"]
        self.assertTrue(dest["name"])
        self.assertIsInstance(dest["affinity"], float)
        self.assertTrue(0 < dest["affinity"] < 1)
        self.assertTrue(dest["evidence"])
        for e in dest["evidence"]:
            self.assertIn("taste", e)
            self.assertIsInstance(e["score"], float)

    def test_alternatives_shape(self):
        self.assertGreaterEqual(len(self.sample["alternatives"]), 2)
        for alt in self.sample["alternatives"]:
            self.assertTrue(alt["name"])
            self.assertIsInstance(alt["affinity"], float)

    def test_venues_shape(self):
        self.assertGreaterEqual(len(self.sample["venues"]), 2)
        for venue in self.sample["venues"]:
            for field in ("name", "subtype", "address", "affinity"):
                self.assertIn(field, venue)

    def test_trace_shape(self):
        self.assertIsInstance(self.sample["trace"], list)
        for step in self.sample["trace"]:
            self.assertIn("tool", step)
            self.assertIn("params", step)
            self.assertIn("outcome", step)

    def test_provenance_and_warnings_present(self):
        self.assertIn("engine", self.sample["provenance"])
        self.assertIn("note", self.sample["provenance"])
        self.assertIsInstance(self.sample["warnings"], list)
        self.assertTrue(self.sample["warnings"])

    def test_matched_inputs_shape(self):
        self.assertTrue(self.sample["matched_inputs"])
        for item in self.sample["matched_inputs"]:
            for field in ("taste", "entity", "urn", "type"):
                self.assertIn(field, item)


class SampleLabellingTest(unittest.TestCase):
    """The sample must never be mistakable for a live result (honesty gate)."""

    def setUp(self):
        self.sample = extract_sample()

    def test_every_displayed_name_is_marked_illustrative(self):
        self.assertIn("illustrative", self.sample["destination"]["name"].lower())
        for alt in self.sample["alternatives"]:
            self.assertIn("illustrative", alt["name"].lower())
        for venue in self.sample["venues"]:
            self.assertIn("illustrative", venue["name"].lower())
        self.assertIn("not a live result", self.sample["destination"]["country"])

    def test_sample_uses_no_real_entity_urns(self):
        for item in self.sample["matched_inputs"]:
            self.assertTrue(item["urn"].startswith("urn:sample:"))
        for e in self.sample["destination"]["evidence"]:
            self.assertTrue(e["taste"].startswith("urn:sample:"))

    def test_banner_element_and_wording_ship(self):
        self.assertIn('id="samplenote"', INDEX)
        self.assertIn("Sample view", INDEX)
        self.assertIn("illustrative", INDEX.lower())
        self.assertIn("does not come from a live taste-graph call", INDEX)

    def test_banner_is_hidden_by_default(self):
        match = re.search(r'<div id="samplenote"[^>]*>', INDEX)
        self.assertIsNotNone(match, "samplenote div not found")
        self.assertIn("hidden", match.group(0))


class TriggerScopeTest(unittest.TestCase):
    def test_service_error_list_matches_backend(self):
        match = re.search(r'var SERVICE_ERRORS = \[(.*?)\];', INDEX, re.DOTALL)
        self.assertIsNotNone(match, "SERVICE_ERRORS list not found")
        listed = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        self.assertEqual(listed, SERVICE_SIDE)

    def test_client_errors_absent_from_trigger_list(self):
        match = re.search(r'var SERVICE_ERRORS = \[(.*?)\];', INDEX, re.DOTALL)
        self.assertTrue(match, "SERVICE_ERRORS list not found")
        listed = set(re.findall(r'"([a-z_]+)"', match.group(1)))
        self.assertFalse(listed & CLIENT_SIDE)


if __name__ == "__main__":
    unittest.main()
