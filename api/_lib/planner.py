"""Deterministic taste-graph trip planner.

The agent is a bounded tool loop: resolve each stated taste to a Qloo entity,
shortlist destination localities by taste affinity, then pull venues inside the
winning locality. Every tool call is recorded in a trace that ships to the UI,
so each recommendation carries visible provenance (input tastes, affinity
scores, per-entity explainability) rather than a bare list.

No LLM is required: the planning policy is fixed and auditable, which keeps the
demo reproducible and the quota spend bounded (searches + 2 insight calls per
plan).
"""

MAX_TASTES = 5
MAX_NAME_LEN = 80

from _lib import qloo_client  # noqa: E402


class PlanError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def normalise_tastes(raw):
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise PlanError("bad_input", "tastes must be a list of strings")
    cleaned = []
    for item in raw:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item:
            cleaned.append(item[:MAX_NAME_LEN])
        if len(cleaned) >= MAX_TASTES:
            break
    if not cleaned:
        raise PlanError("bad_input", "provide at least one taste (film, artist, food, brand)")
    return cleaned


def _affinity(entity):
    value = entity.get("affinity")
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    return None


def _evidence(entity):
    explain = entity.get("explainability") or {}
    contributions = explain.get("entities") or explain.get("contributions") or {}
    evidence = []
    if isinstance(contributions, dict):
        for key, score in contributions.items():
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                continue
            if score_f >= 0.1:
                evidence.append({"taste": key, "score": round(score_f, 3)})
    evidence.sort(key=lambda e: e["score"], reverse=True)
    return evidence[:4]


def _shape_all(entities):
    return [qloo_client._entity_shape(e) for e in entities or []]


def plan_trip(client, tastes_raw):
    tastes = normalise_tastes(tastes_raw)
    trace = []
    warnings = []

    # Tool 1: resolve each stated taste to a Qloo entity.
    resolved = []
    for taste in tastes:
        found = _shape_all(client.search(query=taste, take=3))
        trace.append({"tool": "search", "params": {"query": taste, "take": 3},
                      "outcome": "%d entities" % len(found)})
        if not found:
            warnings.append("no entity matched '%s'; skipped" % taste)
            continue
        resolved.append(found[0])

    if not resolved:
        raise PlanError("no_tastes_resolved",
                        "None of the given tastes matched a Qloo entity. Try well-known artists, films or dishes.")
    urns = [e["urn"] for e in resolved if e.get("urn")]

    # Tool 2: destination shortlist (cities ranked by taste affinity).
    # Live API: urn:entity:destination is the supported type (urn:entity:locality
    # is rejected); signal.interests.entities is required; comma-joined style.
    dest_entities, dest_raw, style = client.insights(
        entity_type="urn:entity:destination", interests=urns, take=10)
    dest_entities = _shape_all(dest_entities)
    trace.append({"tool": "insights", "params": {"filter.type": "urn:entity:destination",
                                                 "interests": len(urns), "take": 10},
                  "outcome": "%d destinations (param style: %s)" % (len(dest_entities), style)})
    if not dest_entities:
        raise PlanError("no_destinations",
                        "Qloo returned no destination affinities for those tastes. Try broader tastes.")

    ranked = sorted(dest_entities, key=lambda e: (_affinity(e) or 0), reverse=True)
    winner = ranked[0]
    alternatives = ranked[1:4]

    # Tool 3: venues inside the winning destination, scoped by the winner's
    # own geohash prefix (city-scale, ~5km cell) - filter.location needs a
    # locality ID, which destination results do not carry.
    winner_geohash = (winner.get("geohash") or "")[:5] or None
    venue_entities, _venue_raw, _vstyle = client.insights(
        entity_type="urn:entity:place", interests=urns, take=12,
        geohash=winner_geohash)
    venue_entities = _shape_all(venue_entities)
    trace.append({"tool": "insights", "params": {"filter.type": "urn:entity:place",
                                                 "geohash": winner_geohash or "(none)", "take": 12},
                  "outcome": "%d venues" % len(venue_entities)})

    venues = [{
        "name": v.get("name"),
        "subtype": v.get("subtype"),
        "address": v.get("address"),
        "affinity": _affinity(v),
        "evidence": _evidence(v),
    } for v in venue_entities[:10]]

    return {
        "tastes": tastes,
        "matched_inputs": [{
            "taste": t,
            "entity": r.get("name"),
            "urn": r.get("urn"),
            "type": r.get("type"),
        } for t, r in zip([x for x in tastes], resolved)],
        "destination": {
            "name": winner.get("name"),
            "country": winner.get("country"),
            "affinity": _affinity(winner),
            "evidence": _evidence(winner),
        },
        "alternatives": [{
            "name": a.get("name"),
            "country": a.get("country"),
            "affinity": _affinity(a),
        } for a in alternatives],
        "venues": venues,
        "warnings": warnings,
        "trace": trace,
        "provenance": {
            "engine": "Qloo Insights API (aggregate taste-affinity signals)",
            "note": "Scores are aggregate audience-level cultural affinity signals between the given "
                    "tastes and places. They are statistical tendencies, not predictions about any "
                    "individual, and venue results reflect data coverage rather than exhaustive listings.",
        },
    }
