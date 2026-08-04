# Routing correctness regression protocol

The pre-snapshot baseline is commit `24abaa8` (the parent of `e19c60a`, which
introduced `SnapshotPlanner` and switched the API dependency wiring to it).
This SHA is the initial regression oracle; invariant validation remains
authoritative when the legacy implementation is wrong.

Never update golden results as a side effect of a test. Golden generation must
run from a temporary worktree at the baseline SHA and must require an explicit
`--update` option. No golden has been generated in this change because updating
goldens was not authorized.

Correctness order:

1. Validate source rows against the current snapshot format.
2. Compare array Dijkstra with the legacy engine at `24abaa8`.
3. Compare validated-geographic array A* with array Dijkstra.
4. Run randomized differential tests and the independent path validator.
5. Only after all of the above pass, benchmark a proven admissible non-zero
   heuristic and memory use.

Commands (run with the project Python environment):

```text
python -m scripts.build_routing_snapshot --output data/routing_snapshot
python -m scripts.validate_routing_snapshot data/routing_snapshot
python -m pytest -q tests/routing/test_snapshot.py
python -m pytest -q tests/routing/test_snapshot_randomized.py
python -m scripts.benchmark_routing_algorithms
python -m scripts.benchmark_snapshot
```

Snapshot v2 added parent-station
indexes, pickup/drop-off flags, GTFS transfer edges, transfer types and minimum
transfer times. Missing explicit bay-to-bay rules are generated only between
members of the same parent station, with a documented default of 120 seconds.
Snapshot v3 adds transfer offset indexes and declared geographic-heuristic
metadata. The current loader accepts v2 and v3, applying compatible fallbacks
when optional v3 metadata is unavailable. It rejects v1 and unknown formats.
Render rebuilds the snapshot before starting the application.

Snapshot A* uses a request-cached Haversine travel-time lower bound for
single-best-route searches. Snapshot construction measures the implied speed
of every positive-displacement transit connection and every usable transfer,
including GTFS times beyond 24:00, then stores the greatest speed with a 0.1%
numerical safety margin. The loader revalidates that bound against the arrays.
The triangle inequality and edge-speed invariant make the heuristic consistent.

If coordinates or metadata are missing/invalid, or a displaced edge has
non-positive duration, loading continues and A* automatically uses `h = 0`
with a diagnostic reason. Alternatives retain Dijkstra ordering because their
candidate count and extra-arrival-window cutoff depend on arrival ordering.

Full-data differential measurements, the named Coquitlam Central bay IDs, and
production RSS/latency numbers require database/snapshot access. They must not
be inferred from the deterministic fixture.

The locally available v2 artifact identifies feed `26JUN_20260717`, SFU
Transportation Centre Bay 1 as stop `1877`, and these Coquitlam Central
selectable stops: `11229`, `12234`, `12235`, `12321`, `12322`, `12647`, `3053`,
`3067`, `3195`, `3204`, `3334`, `3394`, `3533`, `3769`, `3904`, `3906`, `3927`,
`7643`, `8021`, and `8032`. This records IDs only; benchmark claims still require
a dated, reproduced run against the active code.
