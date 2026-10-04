# Algorithms and experiments

## Active production planner

`SnapshotPlanner` is the active production planner. The public request schema
accepts only `astar` and `dijkstra`; unsupported names receive a validation
response rather than reaching an incompatible router.

### Dijkstra

Dijkstra uses scheduled arrival cost for queue priority and result construction.
It reads only connections and transfers reachable from the active stop.

### A* with network travel-time bounds

Single-route A* uses a lower bound computed from the saved transit network.
`NetworkBounds` in `src/routing/snapshot_bounds.py` builds a reverse graph once
per planner instance. For each directed stop pair, it keeps the shortest ride
or transfer duration across all saved connections. This compact graph is shared
by requests; it does not duplicate the full timetable as Python objects.

For each destination, reverse Dijkstra computes the minimum remaining travel
time from every stop. It ignores departure times, waiting, service calendars,
pickup/drop-off restrictions, and transfer limits. Even forbidden transfer
edges are included in this relaxed graph. Removing these constraints can only
make a journey faster, so the result never overestimates a feasible journey:

```text
h_network(stop) = shortest remaining ride + transfer duration, ignoring waiting
queue_priority = actual_arrival_time + h(stop)
```

If validated geographic metadata is available, the planner also calculates the
existing Haversine-distance / maximum-speed bound and uses the larger of the
two bounds. Both are lower bounds, so their maximum is still admissible. Missing
geographic metadata disables only the geographic component: format-2 snapshots
still benefit from the network bound. Stops with no path to the destination in
the relaxed graph can be skipped entirely.

Actual arrival times, reconstruction, and reported durations use the scheduled
cost, never the heuristic-adjusted queue priority. The destination-distance
array is request-local; there is no growing cache of destinations. Diagnostics
expose `network_heuristic_enabled` separately from
`geographic_heuristic_enabled`.

### Reducing work in the search loop

Three complementary changes reduce CPU work in `snapshot_search.py`:

- **Array views:** ordinary NumPy array views retain the memory-mapped backing
  without the extra per-scalar dispatch of the `memmap` subclass. This does not
  copy all routing arrays into memory.
- **Departure lookup:** a request-local index gathers sorted departure times
  for each visited stop. Binary search skips departures before the label's
  arrival and bounds the slice by the existing departure horizon. The temporary
  indexes do allocate memory, but disappear when the request completes.
- **Boarding dominance:** for single-route searches, an earlier boardable label
  at the same stop with no greater transfer cost can cover later new boardings.
  Dominated labels still expand connections on their current trip: remaining
  aboard does not consume a transfer and may pass a no-pickup stop. This pruning
  applies to both single-route Dijkstra and A*.

These changes preserve earliest-arrival search. They do not turn the single
route into a globally reliability-optimal search; reliability ranking still
operates on the candidates the search produces.

### Alternatives

`include_alternatives: false` returns at most one route. `true` returns at most
three public alternatives. Alternatives use arrival-ordered zero-heuristic
collection because heuristic queue ordering is not used to prove the multi-route
candidate window. Neither the network bound nor single-route boarding dominance
is applied to alternative enumeration. Array views and departure indexing still
apply, but long alternative searches may exceed a small CPU budget.

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


## Validation and measured impact

The seeded differential test compares 500 single-route searches with the earliest
arrival from alternative enumeration, which retains the unpruned boarding search.
It also compares A* with Dijkstra. Separate network-bound tests cover parallel
edges, directed reachability, transfers, zero-duration edges, and empty networks.

On the saved production snapshot, two long trips that previously exceeded a
30-second local backend deadline completed in median API times of 0.568 seconds
(Waterfront to SFU) and 0.359 seconds (Metrotown to UBC). Peak process working set
including FastAPI was about 161 MiB. These are local Windows measurements, not
measurements under Render's CPU quota. See [Benchmarks](benchmarks.md) for stop
IDs, departure times, repeat counts, commands, and deployment limitations.
