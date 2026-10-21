# City Break

A taste-matched short-break finder. Name a few things you love (an artist, a
film, a dish, a brand) and a deterministic agent queries the
[Qloo Insights API](https://docs.qloo.com) to recommend one matched city plus
concrete venues inside it - with the taste-affinity evidence for every
recommendation shown inline.

Live demo: https://qloo-city-break.vercel.app

## How the agent works

The planner is a bounded, auditable tool loop (no LLM in the default path,
which keeps results reproducible and API quota spend predictable):

1. **Resolve** - each stated taste is resolved to a Qloo entity via `GET /search`.
2. **Shortlist** - taste entity URNs feed `GET /v2/insights`
   (`filter.type=urn:entity:locality`, diversified per metro area) to rank
   destination cities by aggregate audience affinity.
3. **Populate** - the winning locality scopes a second insights call
   (`filter.type=urn:entity:place`) for venues, diversified by subtype so the
   shortlist mixes food, culture and nightlife.

Every tool call is recorded and rendered as a trace in the UI. Each result
carries its Qloo affinity score and per-entity explainability (which input
tastes contributed, and by how much), so provenance is visible rather than
implied.

## Qloo integration notes

- Base URL: `https://hackathon.api.qloo.com`, credential via the `X-Api-Key`
  header (server-side only - the browser never sees it).
- Surfaces used: `/search`, `/v2/insights`, `/v2/tags`. Legacy
  `/recommendations` is never called.
- Qloo silently ignores invalid parameters (empty 200s), so the client probes
  both documented array parameter styles (comma-joined and repeated) for
  `signal.interests.entities` and remembers which produced results.
- Responses are not persisted; each plan is a fresh bounded set of calls.

## Run it

```bash
pip install -r requirements.txt   # stdlib only - nothing to install
QLOO_API_KEY=your-key python3 -m unittest discover -s tests -v
```

Deploy (any platform supporting Python serverless functions + static files;
designed for Vercel):

```bash
vercel --prod          # with QLOO_API_KEY set in the project environment
```

API surface:

| Route | Method | Purpose |
| ----- | ------ | ------- |
| `/api/health` | GET | Liveness + whether the Qloo key is configured |
| `/api/plan` | POST | `{"tastes": ["radiohead", "ramen"]}` -> destination, venues, evidence, trace |

## Data handling

No personal data is collected, stored or sent to Qloo: requests contain only
the taste keywords the visitor types. Scores describe audience-level cultural
affinity - statistical tendencies, not predictions about any individual - and
venue lists reflect Qloo data coverage rather than exhaustive directories.

## License

MIT
