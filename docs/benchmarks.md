# Benchmarks

## Rules for interpreting results

Benchmarks are local measurements, not service-level guarantees. Record the
date, hardware/runtime context, snapshot source version, service date, stop pair,
departure time, iteration count, alternatives setting, and timeout. Compare
algorithm variants with the same snapshot and request.

Do not compare a warm process with a cold process without labeling startup and
loading separately. Equal-arrival paths may differ, but benchmark verification
should confirm valid path continuity and equivalent earliest arrival.

## Current reproduced snapshot run

Reproduced on 2026-08-04 using:

- Python 3.12.11 on the local Windows development machine.
- Snapshot source `26JUN_20260717`, loaded through the format-2 compatibility
  path.
- Snapshot size: 65,783,383 bytes.
- Service date: 2026-08-04.
- Stops: 646 → 378.
- Requested departure: 05:00:00 (`18000` seconds).
- Three measured iterations per mode.
- Five-second search deadline.

Command:

```powershell
python -m scripts.benchmark_snapshot data/routing_snapshot `
  --origin 646 `
  --destination 378 `
  --service-date 2026-08-04 `
  --departure-seconds 18000 `
  --iterations 3 `
  --timeout-seconds 5
```

| Mode | Median total | Labels pushed/popped | Connections | Transfer records |
| --- | ---: | ---: | ---: | ---: |
| Dijkstra, single | 2.03 ms | 80 / 4 | 188 | 16 |
| A*, single | 1.94 ms | 80 / 4 | 188 | 16 |
| Dijkstra, alternatives | 44.97 ms | 2,343 / 136 | 4,758 | 237 |
| A*, alternatives | 40.44 ms | 2,343 / 136 | 4,758 | 237 |

Snapshot loading was 34.91 ms and the first stop search was 15.34 ms. Runtime
timetable SQL query count was zero.

The snapshot did not provide usable validated geographic-heuristic metadata, so
single-route A* safely used a zero heuristic. Alternative A* also uses zero by
design. The run therefore does not demonstrate geographic heuristic speedup.

## Benchmark commands

Snapshot loading and public algorithms:

```powershell
python -m scripts.benchmark_snapshot --help
```

Experimental database algorithms and cache modes:

```powershell
python -m scripts.benchmark_routing_algorithms --help
python -m scripts.benchmark_route_search --help
```

Database benchmarks require populated PostgreSQL and the five `DB_*` variables.
They should report normalized route signatures as well as timings so a faster
but different answer is not treated as an optimization.

## Future benchmark work

- Rebuild a format-3 snapshot from the same feed and reproduce validated A*
  heuristic measurements on short, medium, and long routes.
- Repeat single and alternatives modes with enough iterations for stable medians.
- Record startup/loading, search, total, labels, connections, transfers,
  heuristic calculations, and cache hits.
- Benchmark on production-equivalent hardware before making latency claims.


## Limited-CPU snapshot optimization (October 4, 2026)

Tested the repository's format-2 snapshot on Windows/Python 3.12.11, using
all calendars (time-of-day mode), an 08:00 departure, and the default single
route. Before optimization, direct backend searches for Waterfront to SFU and
Metrotown to UBC both hit the 30-second deadline. No browser was involved.

After optimization, three requests per pair through FastAPI TestClient gave:

| Stops | Route | Median API seconds | Median CPU seconds | Arrival |
| --- | --- | ---: | ---: | --- |
| 646 → 378 | Dunbar short trip | 0.012 | 0.016 | 08:09:55 |
| 9069 → 1875 | Waterfront → SFU | 0.568 | 0.563 | 09:20:00 |
| 2717 → 12358 | Metrotown → UBC | 0.359 | 0.359 | 08:56:00 |

Peak process working set including FastAPI and startup was 161 MiB. Network
index construction took approximately 0.11 seconds in a separate direct run.
These measurements include local API handling, not Internet latency, Render
cold starts, or a real 0.1-CPU quota. A simple 10x CPU-time scaling would suggest
roughly 5.6 and 3.6 seconds for the two long searches, but is not a deployment
latency guarantee. Concurrent requests will share the same limited CPU.

Changes: zero-copy ndarray views over mmap data; binary searches over sorted
stop departures; removal of dominated new boardings for single-route search;
and an admissible reverse-network travel-time lower bound for A*. This bound
ignores waiting and restrictions, so it never overestimates remaining time.
Continuing on the current vehicle is preserved even when new boarding is dominated.
The network index is shared across requests; no unbounded destination cache exists.

Alternative enumeration retains its original boarding behavior and does not use
the new A* bound. It can still time out on long trips under limited CPU. Default
single-route requests benefit most. Deadlines and resource limits are unchanged.

Reproduce a direct three-run benchmark (service date defaults to none):

```powershell
python -m scripts.benchmark_snapshot data/routing_snapshot --origin 9069 --destination 1875 --departure-seconds 28800 --iterations 3 --single-only --algorithm astar
```

The seeded differential test compares 500 single-route searches to unpruned
alternative enumeration's earliest arrival, as well as A* against Dijkstra.
