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
