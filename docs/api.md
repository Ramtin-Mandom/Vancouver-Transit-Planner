# API reference

Default local base URL: `http://127.0.0.1:8000`.

## Health

### `GET /health`

Process liveness. Returns `200` whenever the application process is running. It
does not prove routing is available.

### `GET /ready`

Routing readiness. Returns `200` when the active planner can serve routes and
`503` while the snapshot is loading, unavailable, incompatible, or expired.
Snapshot responses include format/source information, counts, and service-range
state where available.

## Stop search

### `GET /stops/search?query=Gran&limit=10`

- `query`: at least two non-whitespace characters.
- `limit`: integer from 1 through 20; default 10.

Response:

```json
[
  {
    "stop_id": "string",
    "stop_code": "string or null",
    "stop_name": "string",
    "latitude": 49.0,
    "longitude": -123.0
  }
]
```

## Route planning

### `POST /routes/plan`

Required fields:

| Field | Type | Notes |
| --- | --- | --- |
| `origin_stop_id` | string | Trimmed, non-empty, different from destination |
| `destination_stop_id` | string | Trimmed, non-empty |
| `departure_time` | string | GTFS time, including values beyond `24:00:00` |

Optional fields:

| Field | Default | Validation |
| --- | ---: | --- |
| `algorithm` | `astar` | `astar` or `dijkstra` |
| `cache_mode` | repository default | `request` or `shared`; snapshot accepts it for compatibility |
| `include_alternatives` | `false` | Boolean; public result cap is 3 |
| `minimum_samples` | `10` | At least 1 |
| `max_extra_minutes` | `30` | 0–120 |
| `search_timeout_seconds` | `30` | Greater than 0, at most 120 |
| `reliability_effect` | `0.5` | Non-negative |
| `travel_time_effect` | `0.5` | Non-negative |
| `transfer_effect` | `0` | Non-negative |
| `include_diagnostics` | `false` | Boolean |

The ranking weights must form valid `RoutingPreferences`; invalid or all-zero
weights are rejected. Extra fields are forbidden. `route_number`, service date,
`baseline`, and `mc_raptor` are not accepted by the public schema.

Example request:

```json
{
  "origin_stop_id": "646",
  "destination_stop_id": "378",
  "departure_time": "05:00:00",
  "algorithm": "astar",
  "include_alternatives": false,
  "reliability_effect": 0.5,
  "travel_time_effect": 0.5,
  "transfer_effect": 0
}
```

The response contains origin/destination stops, the internally selected current
Vancouver service date, requested departure, zero to three alternatives, timing,
and optional diagnostics. Each alternative contains rank, times, duration,
transfers, reliability, combined score, the actually selected fallback levels,
insufficient-data state, and fully ordered legs/stops.

An empty completed search returns `200` with an empty `alternatives` array. A
deadline is `504`, not a successful empty response.

## Error behavior

- `4xx`: malformed stop query, invalid request, unsupported algorithm, unknown
  stop, or other client-correctable input.
- `503`: planner unavailable, incompatible/missing production resource, or
  expired feed.
- `504`: genuine routing deadline.
- `500`: unexpected server failure with a safe client message; traceback remains
  only in server logs.

Clients should display concise categories instead of raw internal details.

## Diagnostics

When requested, diagnostics include timings, counters, cache statistics,
requested/executed algorithm, heuristic state and fallback reason, candidate
completion/truncation, resource-limit state, and termination reason. Diagnostics
are optional so the normal response remains backward compatible.
