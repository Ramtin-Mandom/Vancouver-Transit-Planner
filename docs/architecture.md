# Architecture

## System boundary

The project has two operational phases: an offline data pipeline and an online
snapshot service. PostgreSQL belongs to the pipeline. It is not part of the
production request path.

```mermaid
flowchart TB
  subgraph Pipeline["Offline build and refresh"]
    Static["Static GTFS"] --> Validate["Validate and import"]
    Realtime["GTFS-Realtime"] --> Observe["Delay observations"]
    Validate --> DB[("PostgreSQL")]
    Observe --> DB
    DB --> Profiles["Reliability aggregation"]
    Profiles --> DB
    DB --> Builder["Snapshot builder"]
    Builder --> Artifact["Versioned NumPy snapshot"]
  end

  subgraph Production["Online production"]
    Artifact --> Archive["Repository snapshot ZIP"]
    Archive --> Restore["Render restore and validation"]
    Restore --> Planner["SnapshotPlanner"]
    Planner --> API["FastAPI"]
    API --> Frontend["React + Leaflet"]
    Frontend --> Tiles["OpenStreetMap tiles"]
  end
```

## Production request path

FastAPI loads one `RoutingSnapshot` during application startup. Stop lookup and
routing read memory-mapped arrays from that artifact. `SnapshotPlanner` executes
the public `astar` and `dijkstra` choices and serializes the shared routing-domain
models into API responses. Deployment restores `data/routing_snapshot.zip`;
neither the Render build nor the serving process requires PostgreSQL.

The planner constructs a compact reverse graph of minimum ride/transfer durations
once at startup. Single-route A* computes destination-specific lower bounds from
this shared graph, then searches the timetable with request-local departure
indexes and boarding-dominance pruning. The graph is shared; queues, labels,
bounds, and departure indexes belong to each request. Concurrent requests still
compete for the instance's CPU and memory.

See [Algorithms and experiments](algorithms-and-experiments.md) for the lower-bound
proof and pruning rules, and [Benchmarks](benchmarks.md) for measured resource use.

`/health` returns `200` while the process is alive. `/ready` returns `200` only
when the planner can route with a compatible snapshot. Render uses
`/ready` as its health-check path.

## Snapshot format and compatibility

The current writer emits format version 3. The loader accepts formats 2 and 3.
Version 3 includes indexed transfer metadata and validated geographic-heuristic
metadata. A format-2 snapshot can still route; unavailable optional metadata
falls back to compatible behavior. The network travel-time bound is computed
from existing arrays and works without geographic metadata.

The snapshot stores:

- Stop identifiers, names, codes, coordinates, and parent stations.
- Ordered transit connections and GTFS times as integer seconds.
- Service calendars and calendar-date exceptions.
- Transfers and version-3 stop-offset indexes.
- Exact and broader reliability profiles with sample counts.
- A manifest containing format, source, counts, build measurements, service
  range, and heuristic validation.

Unknown or corrupt formats fail clearly during loading. Public snapshot routing
uses all saved trips by time of day; service calendars remain as provenance and
for legacy dated experiments. Calendar expiration does not block readiness.

## Frontend

The frontend uses React, TypeScript, Vite, Leaflet, and React Leaflet. It checks
`/ready`, submits the public route request schema, and renders each alternative
as a separate selectable map path. OpenStreetMap provides runtime tiles. Google
Fonts are optional; CSS fallback fonts preserve readable rendering when the font
request fails.

## Production versus experimentation

Production is deliberately narrow: `SnapshotPlanner`, `astar`, and `dijkstra`.
The database router, baseline model, MC-RAPTOR, experimental A*, request caches,
shared bounded caches, response caching, warm-up coordination, and benchmark
adapters remain available for development comparison. They are not silently
exposed through the production request schema.

## Repository boundaries

- `src/api`: HTTP lifecycle, validation, errors, and serialization.
- `src/routing`: production and experimental planners, domain models, caches,
  snapshot storage, and search.
- `src/reliability`: observation parsing, aggregation, profiles, and ranking.
- `src/data_ingestion`: static-feed validation and transactional loading.
- `scripts`: reproducible build, validation, download, and benchmark commands.
- `frontend`: user interface and browser-facing API client.
