# Algorithms and experiments

## Active production planner

`SnapshotPlanner` is the active production planner. The public request schema
accepts only `astar` and `dijkstra`; unsupported names receive a validation
response rather than reaching an incompatible router.

### Dijkstra

Dijkstra uses scheduled arrival cost for queue priority and result construction.
It reads only connections and transfers reachable from the active stop.

### Validated snapshot A*

For single-route searches, A* can add a request-local cached estimate:

```text
h(stop) = Haversine distance(stop, destination) / validated maximum graph speed
```

The builder checks every positive-distance transit and transfer edge. Missing or
invalid coordinates use zero for that stop. A zero/negative-duration spatial
edge, invalid speed bound, or older metadata disables the geographic heuristic
for the request. Actual arrivals, dominance, reconstruction, and durations
always use `g`, never `g + h`.

### Alternatives

`include_alternatives: false` returns at most one route. `true` returns at most
three public alternatives. Alternatives use arrival-ordered zero-heuristic
collection because geographic A* ordering is not used to prove the multi-route
candidate window.

The search has a generous candidate bound. Diagnostics distinguish complete
collection from candidate truncation. Ranking applies reliability, travel-time,
and transfer preferences to the materialized candidates; this is not presented
as an unbounded complete Pareto frontier.

## Reliability ranking

Each leg resolves one profile through the shared hierarchy documented in
[Data pipeline](data-pipeline.md). Route reliability combines selected profile
probabilities. Returned `fallback_levels` contain only levels actually selected,
and `insufficient_data` reflects the selected samples.

## Experimental models

The repository intentionally retains:

- Baseline/database Dijkstra behavior.
- Database-backed A*.
- MC-RAPTOR.
- Legacy and eager database loading adapters.
- Randomized differential comparison tools.

These models are available through tests, CLIs, and benchmark interfaces—not
the public production schema.

## Cache strategies retained

- Request-local trip and search indexes.
- Process-shared bounded TTL caches.
- Negative trip caching.
- Daily departure indexes.
- Reliability-profile caches.
- Heuristic caches.
- Exact completed-response caching.
- Single-flight cache publication.
- Optional startup warm-up coordination.

Snapshot production does not require these database caches at request time. They
remain relevant to experimental database routing and benchmark comparisons.

## Resource and correctness guards

Snapshot search bounds labels, candidates, and deadline-sensitive work.
Exhaustion produces timeout/resource diagnostics, never a misleading empty
route response. Path reconstruction validates connection continuity in tests and
snapshot validation workflows rather than adding a full validation pass to every
production request.
