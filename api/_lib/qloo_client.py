import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://hackathon.api.qloo.com"

# Qloo documents some collection params as arrays. The API accepts
# comma-joined values and repeated params; invalid params are silently
# ignored (empty 200s), so the client probes both shapes and remembers
# which one produced results.
STYLE_JOINED = "joined"
STYLE_REPEATED = "repeated"


class QlooError(Exception):
    """Base error for Qloo API failures."""


class QlooAuthError(QlooError):
    """Credential rejected (401/403)."""


class QlooRateLimited(QlooError):
    """Quota or rate limit exhausted (429)."""


class QlooUnavailable(QlooError):
    """Network or 5xx failure after bounded retries."""


def _entity_shape(entity):
    """Normalise the fields the app needs out of a Qloo entity object.

    Tolerates both raw API entities and already-shaped dicts (idempotent).
    """
    props = entity.get("properties") or {}
    urn = entity.get("urn") or props.get("urn") or ""
    uuid = entity.get("uuid") or entity.get("id") or props.get("id") or ""
    if not uuid and urn:
        tail = urn.rstrip("/").split(":")[-1]
        if tail and tail not in ("artist", "book", "brand", "locality", "movie",
                                 "person", "place", "podcast", "tv_show", "videogame"):
            uuid = tail
    affinity = entity.get("affinity")
    if affinity is None:
        affinity = props.get("affinity")
    explain = entity.get("explainability")
    if not explain:
        query = entity.get("query")
        explain = query.get("explainability") if isinstance(query, dict) else None
    geocode = props.get("geocode") or {}
    return {
        "name": entity.get("name") or props.get("name") or "",
        "urn": urn,
        "uuid": uuid,
        "type": entity.get("type") or (urn.split(":")[2] if urn.count(":") >= 2 else ""),
        "affinity": affinity,
        "subtype": entity.get("subtype") or props.get("subtype"),
        "country": entity.get("country") or geocode.get("country") or props.get("country"),
        "city": entity.get("city") or geocode.get("name") or props.get("city"),
        "address": entity.get("address") or props.get("address"),
        "explainability": explain,
    }


class QlooClient:
    """Thin stdlib client for the Qloo Insights API.

    Only the documented event surfaces are used: GET /search, GET /v2/insights
    and GET /v2/tags. The credential is supplied per instance and is never
    logged, cached or embedded in responses.
    """

    def __init__(self, credential, base_url=DEFAULT_BASE, timeout=10,
                 max_retries=2, retry_backoff=0.5, transport=None):
        if not credential:
            raise ValueError("credential is required")
        self.credential = credential
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._transport = transport or self._urlopen_transport
        self.entities_param_style = None  # learned on first successful insights call

    def _urlopen_transport(self, url, headers, timeout):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))

    def _get(self, path, params):
        url = "%s%s?%s" % (self.base_url, path, urllib.parse.urlencode(params, doseq=True))
        headers = {"X-Api-Key": self.credential, "Accept": "application/json"}
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                status, data = self._transport(url, dict(headers), self.timeout)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise QlooAuthError("credential rejected (HTTP %d)" % exc.code)
                if exc.code == 429:
                    last_err = QlooRateLimited("rate limited (HTTP 429)")
                    time.sleep(self.retry_backoff * (attempt + 1))
                    continue
                raise QlooError("HTTP %d from %s" % (exc.code, path))
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_err = exc
                time.sleep(self.retry_backoff * (attempt + 1))
        else:
            if isinstance(last_err, QlooRateLimited):
                raise QlooRateLimited("rate limited after %d attempts" % (self.max_retries + 1))
            raise QlooUnavailable("request failed after retries: %s" % last_err)
        if not isinstance(data, dict) or not data.get("success"):
            raise QlooError("unsuccessful payload from %s" % path)
        return data.get("results") or {}

    @staticmethod
    def _pack_param(params, name, values, style):
        if style == STYLE_JOINED:
            params[name] = ",".join(values)
        else:
            params[name] = list(values)

    def search(self, query, types=None, take=3):
        params = {"query": query, "take": take}
        if types:
            self._pack_param(params, "types", types, STYLE_JOINED)
        results = self._get("/search", params)
        entities = results.get("entities") or []
        if not entities and types:
            params["types"] = list(types)
            results = self._get("/search", params)
            entities = results.get("entities") or []
        return [_entity_shape(e) for e in entities]

    def insights(self, entity_type, interests=None, take=10, location=None,
                 diversify_by=None, diversify_take=None):
        base = {
            "filter.type": entity_type,
            "take": take,
        }
        if location:
            base["filter.location"] = location
        if diversify_by:
            base["diversify.by"] = diversify_by
        if diversify_take:
            base["diversify.take"] = diversify_take

        styles = [self.entities_param_style] if self.entities_param_style else [STYLE_JOINED, STYLE_REPEATED]
        last_results = {}
        traced_style = None
        for style in styles:
            params = dict(base)
            if interests:
                self._pack_param(params, "signal.interests.entities", interests, style)
            results = self._get("/v2/insights", params)
            entities = results.get("entities") or []
            last_results = results
            traced_style = style
            if entities:
                self.entities_param_style = style
                return entities, results, style
        return [], last_results, traced_style

    def tags(self, tag_type=None, take=20):
        params = {"take": take}
        if tag_type:
            params["filter.type"] = tag_type
        results = self._get("/v2/tags", params)
        return results.get("tags") or []
