import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from _lib.planner import PlanError, normalise_tastes, plan_trip  # noqa: E402


def ent(name, urn, affinity=None, subtype=None, country=None, geohash=None):
    # models the LIVE hackathon API entity: entity_id, query.affinity,
    # location.geohash, properties.geocode.{name,country_code}
    e = {"name": name, "entity_id": urn.split(":")[-1] or urn,
         "properties": {"urn": urn}}
    if affinity is not None:
        e["query"] = {"affinity": affinity}
    if subtype:
        e["properties"]["subtype"] = subtype
    if country:
        e["properties"]["geocode"] = {"name": name, "country_code": country}
    if geohash:
        e["location"] = {"geohash": geohash}
    return e


class FakeClient:
    def __init__(self, searches=None, localities=None, places=None):
        self.searches = searches or {}
        self.localities = localities or []
        self.places = places or []
        self.insight_calls = []

    def search(self, query, types=None, take=3):
        return self.searches.get(query, [])

    def insights(self, entity_type, interests=None, take=10, location=None,
                 diversify_by=None, diversify_take=None, geohash=None):
        self.insight_calls.append({"type": entity_type, "geohash": geohash})
        if entity_type == "urn:entity:destination":
            return list(self.localities), {}, "joined"
        return list(self.places), {}, "joined"


class PlannerTest(unittest.TestCase):
    def test_normalise_tastes_limits_and_cleans(self):
        tastes = normalise_tastes([" Radiohead ", "", "x" * 200, 42])
        self.assertEqual(tastes[0], "Radiohead")
        self.assertEqual(len(tastes[1]), 80)
        self.assertEqual(len(tastes), 2)
        with self.assertRaises(PlanError):
            normalise_tastes([])

    def test_happy_path(self):
        client = FakeClient(
            searches={"radiohead": [ent("Radiohead", "urn:entity:artist:1", 0.9)],
                      "ghibli": [ent("Studio Ghibli", "urn:entity:brand:2", 0.8)]},
            localities=[ent("Lyon", "urn:entity:destination:abc", 0.42, country="FR",
                            geohash="u05ks1abc123"),
                        ent("Osaka", "urn:entity:destination:def", 0.35, country="JP",
                            geohash="xn0pm2def456")],
            places=[ent("Ramen Shop A", "urn:entity:place:p1", 0.61, subtype="ramen")],
        )
        result = plan_trip(client, ["radiohead", "ghibli"])
        self.assertEqual(result["destination"]["name"], "Lyon")
        self.assertEqual(result["destination"]["affinity"], 0.42)
        self.assertEqual(result["destination"]["country"], "FR")
        self.assertEqual(result["alternatives"][0]["name"], "Osaka")
        self.assertEqual(result["venues"][0]["subtype"], "ramen")
        # destination call uses the supported urn:entity:destination type
        dest_call = [c for c in client.insight_calls if c["type"] == "urn:entity:destination"][0]
        self.assertTrue(dest_call)
        # venue call must be scoped to the winning destination's geohash prefix
        place_call = [c for c in client.insight_calls if c["type"] == "urn:entity:place"][0]
        self.assertEqual(place_call["geohash"], "u05ks")
        # trace covers all three tool stages
        tools = [t["tool"] for t in result["trace"]]
        self.assertEqual(tools, ["search", "search", "insights", "insights"])
        self.assertIn("aggregate", result["provenance"]["note"])

    def test_unmatched_taste_is_skipped_with_warning(self):
        client = FakeClient(
            searches={"radiohead": [ent("Radiohead", "urn:entity:artist:1")],
                      "zzzz": []},
            localities=[ent("Lyon", "urn:entity:locality:abc", 0.4)],
            places=[],
        )
        result = plan_trip(client, ["radiohead", "zzzz"])
        self.assertTrue(any("zzzz" in w for w in result["warnings"]))
        self.assertEqual(len(result["matched_inputs"]), 1)

    def test_no_tastes_resolved_raises(self):
        client = FakeClient(searches={})
        with self.assertRaises(PlanError) as ctx:
            plan_trip(client, ["nothing", "matches"])
        self.assertEqual(ctx.exception.code, "no_tastes_resolved")

    def test_no_destinations_raises(self):
        client = FakeClient(searches={"x": [ent("X", "urn:entity:artist:1")]},
                            localities=[])
        with self.assertRaises(PlanError) as ctx:
            plan_trip(client, ["x"])
        self.assertEqual(ctx.exception.code, "no_destinations")

    def test_ranking_is_by_affinity_not_order(self):
        client = FakeClient(
            searches={"x": [ent("X", "urn:entity:artist:1")]},
            localities=[ent("Low", "urn:entity:destination:l1", 0.11,
                            geohash="aaaaa1low"),
                        ent("High", "urn:entity:destination:l2", 0.88,
                            geohash="bbbbb2high")],
            places=[],
        )
        result = plan_trip(client, ["x"])
        self.assertEqual(result["destination"]["name"], "High")
        place_call = [c for c in client.insight_calls if c["type"] == "urn:entity:place"][0]
        self.assertEqual(place_call["geohash"], "bbbbb")


if __name__ == "__main__":
    unittest.main()
