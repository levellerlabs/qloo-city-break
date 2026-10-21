import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from _lib import qloo_client  # noqa: E402
from _lib.qloo_client import (  # noqa: E402
    QlooAuthError,
    QlooClient,
    QlooError,
    QlooRateLimited,
    STYLE_JOINED,
    STYLE_REPEATED,
)


def entity(name, urn, affinity=None, subtype=None, country=None):
    props = {"urn": urn}
    if affinity is not None:
        props["affinity"] = affinity
    if subtype:
        props["subtype"] = subtype
    if country:
        props["geocode"] = {"country": country, "name": name}
    return {"name": name, "urn": urn, "properties": props}


class FakeTransport:
    """Callable transport: routes (path, params-style) to canned payloads."""

    def __init__(self, responses):
        self.responses = responses  # list of (matcher, status, payload)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        for matcher, status, payload in self.responses:
            if matcher(url):
                if status != 200:
                    import urllib.error

                    raise urllib.error.HTTPError(url, status, "err", {}, None)
                return 200, payload
        raise AssertionError("no canned response for %s" % url)


def ok_payload(entities, extra=None):
    results = {"entities": entities, "duration": 0.01}
    if extra:
        results.update(extra)
    return {"success": True, "results": results}


class ClientConfigTest(unittest.TestCase):
    def test_requires_key(self):
        with self.assertRaises(ValueError):
            QlooClient("")

    def test_auth_header_used(self):
        seen = {}

        def transport(url, headers, timeout):
            seen["headers"] = headers
            return 200, ok_payload([entity("X", "urn:entity:artist:1")])

        client = QlooClient("secret-key", transport=transport)
        client.search("radiohead")
        self.assertEqual(seen["headers"]["X-Api-Key"], "secret-key")

    def test_http_401_raises_auth(self):
        transport = FakeTransport([(lambda u: True, 401, None)])
        client = QlooClient("k", transport=transport)
        with self.assertRaises(QlooAuthError):
            client.search("radiohead")

    def test_http_429_retries_then_raises(self):
        transport = FakeTransport([(lambda u: True, 429, None)])
        client = QlooClient("k", transport=transport, retry_backoff=0)
        with self.assertRaises(QlooRateLimited):
            client.search("radiohead")
        self.assertEqual(len(transport.calls), 3)  # initial + 2 retries

    def test_unsuccessful_payload_raises(self):
        transport = FakeTransport([(lambda u: True, 200, {"success": False})])
        client = QlooClient("k", transport=transport)
        with self.assertRaises(QlooError):
            client.search("radiohead")


class ParamStyleTest(unittest.TestCase):
    def test_insights_falls_back_to_repeated_style(self):
        # joined style -> empty; repeated style -> results
        def matcher(url):
            return "/v2/insights" in url

        transport = FakeTransport([
            (lambda u: "/v2/insights" in u and "signal.interests.entities=a%2Cb" in u,
             200, ok_payload([])),
            (lambda u: "/v2/insights" in u and "signal.interests.entities=a" in u
             and "signal.interests.entities=b" in u,
             200, ok_payload([entity("Paris", "urn:entity:locality:abc", 0.7)])),
            (lambda u: "/v2/insights" in u, 200,
             ok_payload([entity("Le Comptoir", "urn:entity:place:p9", 0.55)])),
        ])
        client = QlooClient("k", transport=transport)
        entities, _raw, style = client.insights("urn:entity:locality", interests=["a", "b"], take=5)
        self.assertEqual(style, STYLE_REPEATED)
        self.assertEqual(len(entities), 1)
        # learned style is reused on the next call
        entities2, _r2, style2 = client.insights("urn:entity:place", interests=["a"], take=5)
        self.assertEqual(style2, STYLE_REPEATED)

    def test_search_type_join_then_repeat(self):
        transport = FakeTransport([
            (lambda u: "types=urn%3Aentity%3Aartist%2Curn%3Aentity%3Amovie" in u, 200, ok_payload([])),
            (lambda u: "types=urn%3Aentity%3Aartist" in u and "types=urn%3Aentity%3Amovie" in u,
             200, ok_payload([entity("Blade Runner", "urn:entity:movie:9")])),
        ])
        client = QlooClient("k", transport=transport)
        found = client.search("dystopia", types=["urn:entity:artist", "urn:entity:movie"], take=3)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "Blade Runner")


class EntityShapeTest(unittest.TestCase):
    def test_uuid_from_urn_tail(self):
        shaped = qloo_client._entity_shape(entity("Paris", "urn:entity:locality:1234-abcd", 0.5))
        self.assertEqual(shaped["uuid"], "1234-abcd")
        self.assertEqual(shaped["affinity"], 0.5)

    def test_uuid_not_taken_from_bare_type(self):
        shaped = qloo_client._entity_shape({"name": "X", "urn": "urn:entity:artist", "properties": {}})
        self.assertEqual(shaped["uuid"], "")

    def test_explainability_extracted(self):
        raw = {"name": "Lyon", "urn": "urn:entity:locality:zz",
               "query": {"explainability": {"entities": {"urn:entity:artist:1": 0.42, "urn:entity:movie:2": 0.05}}},
               "properties": {}}
        shaped = qloo_client._entity_shape(raw)
        self.assertEqual(shaped["explainability"]["entities"]["urn:entity:artist:1"], 0.42)


if __name__ == "__main__":
    unittest.main()
