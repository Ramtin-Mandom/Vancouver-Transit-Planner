# Vancouver Transit Planner

A transit planner that imports TransLink GTFS data into PostgreSQL, finds
scheduled journeys, collects GTFS-Realtime delay observations, and can rank
route alternatives using historical delay and transfer reliability.

## PostgreSQL data ingestion

The project requires Python 3.10+, PostgreSQL, `psycopg` 3, and
`python-dotenv`. Tests use `pytest`.

Create and activate a virtual environment, then install the dependencies:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Python 3.11 or newer can be selected instead if installed. Confirm that the
activated environment is using a supported version:

```powershell
python --version
```

Copy `.env.example` to `.env` and set `DB_HOST`, `DB_PORT`, `DB_NAME`,
`DB_USER`, and `DB_PASSWORD`. The real `.env` is ignored by Git. Create the
database tables manually with `database/schema.sql`; the Python loader never
executes that schema file.

Validate every source header, row width, and value conversion without connecting
to PostgreSQL:

```powershell
python -m src.data_ingestion.cli --dry-run
```

Audit incomplete required values and cross-file foreign keys without changing
the extracted files:

```powershell
python -m src.data_ingestion.cleaner --dry-run
```

After reviewing the report, remove malformed rows, rows with empty
schema-required fields, and orphaned foreign-key references:

```powershell
python -m src.data_ingestion.cleaner
```

The cleaner preserves blank optional GTFS fields. Files are rewritten
atomically and only when at least one row must be removed.

For the first import into empty target tables:

```powershell
python -m src.data_ingestion.cli
```

Normal mode refuses to run if any managed target table already contains data.
To deliberately replace the imported feed, use:

```powershell
python -m src.data_ingestion.cli --replace
```

`--replace` runs `TRUNCATE ... RESTART IDENTITY CASCADE` only for the required
GTFS-backed tables in the `transit` schema. Loading is transactional, so a
failure rolls back both truncation and inserted rows.

## Scheduled transit routing

The scheduled router reads the existing GTFS tables in the `transit` schema.
It uses the same `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`
values from `.env` as the data-ingestion commands. Routing is read-only: it
does not create, replace, or modify database tables.

Make sure PostgreSQL is running and the GTFS feed has been imported, then run:

```powershell
python -m src.routing.cli `
  --origin STOP_ID `
  --destination STOP_ID `
  --date 2026-07-27 `
  --departure 08:00:00
```

Replace `STOP_ID` with IDs from the imported `transit.stops` table. The date is
the GTFS service date, and departure must use `HH:MM:SS`. GTFS times after
midnight may exceed 24 hours, so values such as `25:10:00` are supported:

```powershell
python -m src.routing.cli `
  --origin 50001 `
  --destination 60001 `
  --date 2026-07-27 `
  --departure 25:10:00
```

The result lists the origin and destination, each vehicle leg, route name or
number, trip ID, scheduled times, transfer count, and total scheduled travel
time. If the stops exist but no active scheduled journey can be found, the
command prints `No scheduled route found.`

### Troubleshooting

- Connection errors: confirm PostgreSQL is running, the five `DB_*` values are
  correct, and the user can connect to the selected database.
- Missing-table errors: apply `database/schema.sql` manually to the intended
  development database, then retry.
- Invalid-row errors: use the reported filename, row, column, value, and
  expected type to correct or replace the source feed. Run `--dry-run` again
  before importing.
- Existing-data errors: use a fresh database, or review the target carefully
  before explicitly choosing `--replace`.
- Unknown routing stops: check that both IDs exist in `transit.stops`.
- Unexpected routing results: confirm that the requested date is within the
  feed's calendar range and that its `calendar_dates` exceptions are correct.

## Tests

The unit tests use in-memory fixtures and do not require a running PostgreSQL
database. With the virtual environment activated, run the complete suite:

```powershell
python -m pytest
```

Run only the routing tests:

```powershell
python -m pytest tests/routing
```

Run one routing test file:

```powershell
python -m pytest tests/routing/test_planner.py
```

Add `-v` for individual test names or `-q` for compact output:

```powershell
python -m pytest tests/routing -v
```

If the virtual environment is not activated, invoke its Python executable
directly:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/routing
```

Prefer `python -m pytest` over invoking `pytest` directly. This ensures pytest
uses the same Python interpreter and installed packages as the selected virtual
environment. If a traceback mentions an unsupported global installation such
as `Python38`, recreate and activate `.venv` with Python 3.10 or newer using the
commands above.

## HTTP API

The FastAPI service reads the existing PostgreSQL GTFS and precomputed
reliability profile data. It does not call the live TransLink API while
planning routes and does not create a new database schema.

Install all application and testing dependencies from the repository root:

```powershell
python -m pip install -r requirements.txt
```

Configure the same required database variables used by the CLI in `.env`:

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=vancouver_transit
DB_USER=your_database_user
DB_PASSWORD=your_database_password
```

Start the development server from the repository root:

```powershell
python -m uvicorn src.api.main:app --reload
```

Interactive Swagger documentation is available at
http://127.0.0.1:8000/docs.

Search for stops:

```text
GET http://127.0.0.1:8000/stops/search?query=Granville&limit=10
```

Plan reliability-ranked routes:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/routes/plan `
  -ContentType "application/json" `
  -Body '{
    "origin_stop_id": "646",
    "destination_stop_id": "31",
    "departure_time": "08:00:00",
    "route_number": 5,
    "minimum_samples": 10,
    "max_extra_minutes": 30,
    "search_timeout_seconds": 30.0,
    "reliability_effect": 0.5,
    "travel_time_effect": 0.5,
    "transfer_effect": 0.0
  }'
```

GTFS departure times beyond midnight, such as `25:10:00`, are supported.
The public request does not accept a travel date yet. The server derives the
internal GTFS `service_date` from the current `America/Vancouver` calendar date;
that internal date still controls service calendars, exceptions, daily indexes,
and timetable-dependent response caching. Reliability profiles are shared
across dates and use `route_id + direction_id + time_window`.
Local frontend CORS defaults to explicit `localhost` and `127.0.0.1` origins
on ports 3000 and 5173. Override them with a comma-separated
`API_CORS_ORIGINS` environment variable.

API tests use deterministic fakes and require no PostgreSQL connection, API
key, or internet access:

```powershell
python -m pytest tests/api
```

### Real GTFS integration tests

Real-data tests are marked `integration` and are excluded from the default test
run. They use the PostgreSQL database configured by `.env`, perform read-only
queries against the existing `transit` schema, and do not copy or modify GTFS
tables. PostgreSQL connections, sampled GTFS cases, and the planner are
initialized once per test session and reused across all scenarios.

The deterministic real-data suite covers:

- direct journeys;
- journeys with one or more transfers, when present in the feed;
- origin/destination pairs served by multiple routes;
- requests with no available route;
- departures immediately before and after a scheduled trip;
- different active service dates;
- GTFS service-day times after `24:00:00`, when present in the feed.

The reliability-scoring check is skipped until reliability scoring is exposed
by the planner.

Run all fast mocked tests explicitly:

```powershell
python -m pytest -m "not integration" -v
```

Run the real PostgreSQL integration test and show its case reports:

```powershell
python -m pytest -m integration -s
```

Run the user-friendly integration framework directly:

```powershell
python -m src.routing.integration_runner --limit 10
```

Optional runner arguments include:

```powershell
python -m src.routing.integration_runner `
  --limit 10 `
  --departure-buffer-minutes 1 `
  --verbose
```

Add `--fail-fast` to stop after the first failed journey. The runner returns
exit code `0` when every executed case passes, `1` when a case fails, and `2`
when configuration or database setup prevents the run.

Each run preserves the detailed per-case itinerary report and adds a profiling
summary containing:

- one-time database initialization;
- GTFS integration-case loading;
- graph/routing structure construction;
- minimum, maximum, average, and total route-search time;
- result-formatting time;
- total suite execution time.

The router queries GTFS data lazily rather than constructing a separate
in-memory graph, so graph/routing structure construction is normally close to
zero. On the current development feed, ten diversified real routes complete in
about two seconds after connection reuse, compared with roughly 130–140 seconds
when each lookup opened a new PostgreSQL connection. Actual timing depends on
the computer, PostgreSQL configuration, and imported feed.

The relevant database lookups already use
`idx_stop_times_stop_departure` and the `stop_times` primary key. Profiling with
`EXPLAIN ANALYZE` showed no additional index was warranted, so the integration
framework does not create or modify database indexes.

## Delay reliability

Reliability in this milestone means observed transit delay and the probability
of completing scheduled transfers. It does not use weather data.

### Configure and migrate

Obtain a TransLink Open API key and add it to the local `.env` file:

```dotenv
TRANSLINK_API_KEY=your_key_here
```

The default Trip Updates endpoint is configured in
`src/reliability/config.py`. It can be overridden without changing code:

```dotenv
TRANSLINK_GTFS_RT_URL=https://gtfsapi.translink.ca/v3/gtfsrealtime?apikey={api_key}
```

Install the updated dependencies:

```powershell
python -m pip install -r requirements.txt
```

For an existing populated database, review and apply the non-destructive
reliability migration:

If PowerShell reports that `psql` is not recognized, PostgreSQL's `bin`
directory is not on your `PATH`. PostgreSQL 18 is normally installed at
`C:\Program Files\PostgreSQL\18\bin`. Add it for the current terminal:

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\18\bin"
psql --version
```

To add it permanently for your Windows user, run this as one line, then close
and reopen PowerShell (or restart the Codex terminal):

```powershell
$p="C:\Program Files\PostgreSQL\18\bin"; $u=[Environment]::GetEnvironmentVariable("Path","User"); if (($u -split ";") -notcontains $p) {[Environment]::SetEnvironmentVariable("Path",(($u.TrimEnd(";"))+";"+$p),"User")}
```

If PostgreSQL is installed under a different version number, replace `18` in
the path. You can also run it without changing `PATH` by using PowerShell's
call operator:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" --version
```

Once `psql --version` succeeds, apply the migrations:

```powershell
psql -d vancouver_transit -f database/migrations/001_reliability_data.sql
psql -d vancouver_transit -f database/migrations/002_delay_classification.sql
psql -d vancouver_transit -f database/migrations/003_route_direction_window_reliability.sql
```

The migration preserves imported GTFS data. It adds `stop_sequence` to existing
delay observations, corrects snapshot uniqueness, and creates
`route_reliability`. Review the documented stop-sequence backfill before
running it, especially if delay observations already exist.
Migration `003` is forward-only and preserves both raw observations and the
old `route_reliability` table while the replacement is validated. It adds the
trip sample layer, route/direction/window profiles, and precomputed fallback
profiles.

### Collect and aggregate

Collect one realtime Trip Updates snapshot:

```powershell
python -m src.reliability.cli collect
```

One snapshot is useful for verifying the pipeline, but it is not enough for
trustworthy reliability estimates. Run collection repeatedly over days or
weeks using an external scheduler. Exact duplicate snapshots are ignored.
Malformed entities and unknown trip/stop updates are counted without
discarding valid observations from the same feed.

Incrementally update route/direction/time-window reliability profiles:

```powershell
python -m src.reliability.cli aggregate
```

This command is idempotent and safe while collection continues. For validation
or recovery, rebuild only derived data from the append-only source:

```powershell
python -m src.reliability.cli aggregate --full-rebuild
```

The independent sample unit is one operated trip, service date, and time
window. Aggregation first keeps the newest poll for each trip/date/stop/sequence
and then takes the median delay across those latest stops. Thus repeated
five-minute polls and trips with many stops do not receive extra weight.
Profiles use `route_id + direction_id + time_window`; weekday and stop are not
dimensions. A null GTFS direction is retained as an explicit unknown-direction
bucket.

Windows are overnight 00:00–05:59, morning peak 06:00–09:59, midday
10:00–14:59, afternoon peak 15:00–18:59, and evening 19:00–23:59. GTFS
service-day times beyond 24:00 are reduced modulo 24 only for window lookup;
their service date is unchanged. The application definition is in
`src/reliability/classification.py`, with its SQL counterpart installed by
migration `003`.

Delay outcomes use a complete three-way classification:

- earlier than two minutes early (`delay < -120`): early and penalized;
- from two minutes early through five minutes late (`-120 <= delay <= 300`):
  on time;
- more than five minutes late (`delay > 300`): late and penalized.

Signed average delay is retained because its direction is necessary when
simulating transfer timing. It is not sufficient as a reliability measure on
its own: large early and late values can cancel to an average near zero even
when service is inconsistent. Profiles therefore also report mean absolute
delay and separate early, on-time, and late probabilities. Early arrivals are
penalized because leaving materially ahead of schedule can cause passengers to
miss a vehicle just as a late connection can.

Routing multiplies each leg's adjusted probability. Exact profiles shrink
toward precomputed route+direction, route, and network parents using
`weight = n / (n + 20)`. The raw on-time probability, adjusted probability,
sample count, fallback level, and insufficient-data flag remain available.
These short-run profiles do not claim weekday, seasonal, weather, or long-term
effects.

#### Windows Task Scheduler setup used for data collection

The current data-collection setup uses Windows Task Scheduler:

- collect one GTFS-Realtime snapshot every five minutes;
- rebuild reliability profiles once per day.

For both tasks, configure **Start in** as the repository's absolute directory,
for example:

```text
C:\Users\YOUR_NAME\path\to\Vancouver-Transit-Planner
```

Use the virtual environment's absolute Python executable as **Program/script**:

```text
C:\Users\YOUR_NAME\path\to\Vancouver-Transit-Planner\.venv\Scripts\python.exe
```

For the five-minute collection task, use:

```text
-m src.reliability.cli collect
```

Create a daily aggregation task with:

```text
-m src.reliability.cli aggregate --minimum-samples 10
```

Set the collection task to avoid starting a second instance when the previous
run is still active. The API key and database credentials remain in the local
`.env`; do not put secrets directly in Task Scheduler arguments.

Collecting every five minutes provides snapshots throughout each trip and helps
retain the latest useful prediction. It does not artificially multiply the
statistical weight of that trip: daily aggregation selects only the latest
observation for each trip, stop, stop sequence, and service date. Rebuilding
profiles daily is therefore sufficient even though collection runs much more
frequently.

Inspect a route-wide or contextual profile:

```powershell
python -m src.reliability.cli report --route-id ROUTE_ID

python -m src.reliability.cli report `
  --route-id ROUTE_ID `
  --stop-id STOP_ID `
  --weekday 0 `
  --hour 8
```

Weekdays use Python numbering: Monday is `0` and Sunday is `6`.

Profile lookup requires the configured minimum sample count and falls back in
this order:

1. route + stop + weekday + hour;
2. route + stop + hour;
3. route + weekday + hour;
4. route + hour;
5. entire route;
6. system-wide data;
7. insufficient data.

### Rank reliable alternatives

The existing scheduled-routing command and output are unchanged unless
`--reliable` is supplied:

```powershell
python -m src.routing.cli `
  --origin STOP_ID `
  --destination STOP_ID `
  --date 2026-07-27 `
  --departure 08:00:00 `
  --reliable `
  --alternatives 5 `
  --max-extra-minutes 30 `
  --minimum-samples 10 `
  --search-timeout-seconds 30 `
  --reliability-effect 0.5 `
  --travel-time-effect 0.5
```

At the Python API level, `route_number` is the maximum number of routes to
return. Results are already ordered from best to worst:

```python
from src.routing.route_results import RoutingPreferences

routes = planner.get_ranked_routes(
    origin_stop_id,
    destination_stop_id,
    service_date,
    departure_time,
    resolver,
    route_number=5,
    preferences=RoutingPreferences(
        reliability_effect=0.5,
        travel_time_effect=0.5,
    ),
)
```

Use `planner.get_ranked_route_result(...)` when timing and label-pruning
diagnostics are also needed. The compatibility method
`plan_reliable_alternatives(...)` returns that richer result as well.
`route_number` must be at least one; requesting more routes than exist simply
returns every available route.

Reliable alternatives are generated by a bounded, time-dependent multi-label
Pareto search. Unlike repeated earliest-arrival searches, this keeps a slower
label whenever it offers compensating reliability. Each label records arrival,
negative-log reliability, transfers, current trip, visited stops, and its ride
sequence. Labels are compared only when stop and current-trip state are
compatible, because otherwise they may not have the same future boardings.

A label dominates another compatible label only when it arrives no later, has
at least as much reliability, uses no more transfers, and is strictly better
in at least one dimension. This removes only labels that cannot produce a
better feasible continuation; a route is never discarded merely because it is
not the earliest at a stop.

Route reliability uses each leg profile's `on_time_probability`:

```text
reliability_cost = sum(-log(max(leg_probability, 1e-9)))
route_reliability = exp(-reliability_cost)
speed_ratio = fastest scheduled duration / candidate scheduled duration

score = 100 × (0.50 × route_reliability + 0.50 × speed_ratio)
```

Thus two 75% legs have 56.25% route reliability, rather than 75%.
Multiplication assumes leg on-time events are independent. Transfer-success
probability is not multiplied separately: the delay observations already feed
the leg profiles, and adding another value derived from those observations
could double-count the same signal. Missing profiles use probability `1e-9`
and are explicitly marked as insufficient data.

The default score is deterministic, clamped to `0–100`, and gives equal effect
to reliability and scheduled speed. `RoutingPreferences` can increase either
effect or optionally add a transfer effect. Weights must be nonnegative, at
least one must be positive, and they are normalized automatically, so `5/5`
behaves like `0.5/0.5`. Increasing `reliability_effect` favors reliable slower
routes; increasing `travel_time_effect` favors faster routes. Ties use
reliability, arrival, transfers, and the stable ordered
trip/boarding/alighting identity.

Defaults are five results, at most three transfers, arrival no more than 30
minutes beyond the fastest feasible arrival, and a three-hour search horizon.
There is no arbitrary labels-per-stop cap. Cyclic paths are rejected.
Departures are fetched in bounded windows without `OFFSET`; departures, trip
continuations, transfers, and resolved profiles are cached for one request.
The command also has a 30-second wall-clock deadline by default, propagated to
PostgreSQL as a statement timeout, so a search fails with a clear error instead
of running indefinitely.

Timing separates initial stop/service loading, core search, final ranking, and
total request time. The under-100-ms target applies to warmed candidate search
plus ranking; connection setup and cold PostgreSQL/profile loading are
excluded and separately measurable. Run the real-data integration report with:

```powershell
python -m pytest tests/routing/test_real_database.py -m integration -s
```

The normal suite includes a deterministic synthetic performance guard with a
generous threshold; it is not evidence that real GTFS requests meet 100 ms.

### Reliability tests

Normal reliability tests mock HTTP and PostgreSQL and require neither an API
key nor internet access:

```powershell
python -m pytest -m "not integration" -v
```

Configured PostgreSQL integration tests remain opt-in:

```powershell
python -m pytest -m integration -s
```

Any future live-feed test must additionally require:

```powershell
$env:RUN_LIVE_GTFS_RT_TESTS = "1"
```

## Optimization

The reliable Pareto search has been optimized in three measured stages. The
goal throughout was to reduce database overhead without changing routing,
dominance, reliability, scoring, reconstruction, or deterministic ordering.
All comparisons remove timing and diagnostics before checking equality.

### Search profiling

The original timing response exposed only data loading, search, ranking, and
total time, which made database latency indistinguishable from queue and label
CPU work. Optional profiling is now enabled with:

```json
{
  "include_diagnostics": true
}
```

Normal requests leave profiling disabled to avoid its measurement overhead.
Profiled responses aggregate query, cache, queue, label, dominance, transfer,
departure, trip, reliability-profile, filtering, and reconstruction activity.
Timeout responses retain the completed partial diagnostics. Logging emits one
summary per profiled request rather than one message per label or query.

The representative request initially took approximately 8,020 ms and showed
the N+1 data-access pattern clearly:

| Operation | Count |
|---|---:|
| Departure queries | 3,115 |
| Trip-connection queries | 6,782 |
| Transfer queries | 3,115 |
| Reliability profile cache misses | 11,810 |
| Labels created | 419,879 |
| Dominance comparisons | 713,215 |

These measurements showed that data access needed to be optimized before
changing search pruning or Pareto semantics.

### Request-local bulk indexes

The first data-access optimization loaded request-relevant departures and
transfers in bulk, batch-loaded trip connections, and prefetched reliability
profiles. `SearchDataIndex` groups departures by stop, transfers by origin
stop, and connections by trip. Departures and trip sequences are sorted, and
binary search selects the applicable time or stop-sequence range. GTFS times
after `24:00:00` remain represented as `timedelta` values.

Transit preload runs in a read-only repeatable-read snapshot. Trip-ID lists are
deduplicated and split into bounded parameterized batches. The bulk connection
query uses a windowed consecutive-stop scan instead of the slower correlated
next-stop query. Existing indexes are reused:

- `idx_stop_times_stop_departure`;
- the stop-times trip/sequence primary key;
- `idx_transfers_from_stop`;
- `idx_route_direction_reliability_lookup`;
- the reliability-fallback primary key.

This eager implementation eliminated SQL from the queue-processing loop, but
it loaded every trip referenced by every departure in the full request window:
5,189 trips and 148,097 connections. That reduced N+1 latency but spent roughly
1.2-1.6 seconds loading trips that the search never reached.

### Frontier-driven trip loading

The production default is now the internal `frontier` trip-loading mode.
Departures, transfers, active services, stop information, and reliability
profiles remain inexpensive bulk preloads. Complete trip chains are loaded
only after a popped label encounters departures that pass the existing cheap
boarding checks:

- stop and departure-time eligibility;
- service-day and search-horizon bounds;
- transfer rules and minimum transfer time;
- maximum transfers;
- the existing same-trip boarding behavior.

For each label, all newly relevant trip IDs are collected before processing
that label, deduplicated, and sent through the existing parameterized bulk
trip query. `RequestTripConnectionLoader` owns only request-local state:
loaded trips, known-empty trips, pending/failed IDs, ordered connections, and
batch statistics. A requested trip, including one with no returned rows, is
never fetched twice during the request. This preserves priority-search
semantics and does not delay or requeue labels to manufacture larger batches.

The eager mode remains available internally for direct comparison by passing
`trip_loading_mode="eager"`. It is not exposed as a normal API request field.
The legacy mode is retained by the benchmark adapter only.

### Baseline and exact A* routing

The API request's optional `algorithm` field selects `baseline` (or its
`dijkstra` alias), `astar`, or `mc_raptor`. It defaults to `mc_raptor`, so
clients can omit it. Include `"algorithm": "baseline"` or
`"algorithm": "astar"` in `POST /routes/plan` to select either previous
implementation explicitly. A* uses only
straight-line Haversine travel time at the optimistic speed configured by
`ROUTING_MAX_TRANSIT_SPEED_KMH` (default `120`). Missing or invalid coordinates
produce a zero heuristic. The heuristic affects heap ordering only; Pareto
dominance, reliability, transfers, reconstruction, and final ranking are
shared with the baseline.

Reliability-aware McRAPTOR uses bounded transit-boarding rounds and exact
ordered-stop route patterns. Each round collects patterns serving marked
stops, scans each collected pattern once, and retains Pareto labels by
scheduled arrival and negative-log reliability cost. Walking transfers do not
consume a boarding round. Reliability is resolved once per boarded transit
leg and combined multiplicatively as `exp(-sum(reliability_cost))`.

For a one-command warmed comparison using identical requests:

```powershell
py -3.10 -m scripts.benchmark_routing_algorithms --runs 3 --max-extra-minutes 10
```

Override `--origin`, `--destination`, `--date`, and `--departure` to reproduce
direct, transfer, cross-region, unreachable, overnight, and multi-alternative
cases. The command exits nonzero if normalized route signatures differ.

### Measured results

The benchmark alternates execution order across trials to reduce PostgreSQL
cache-order bias:

```powershell
py -3.12 -m scripts.benchmark_route_search --runs 3 --compare-legacy
```

For the representative local request, the measured medians were:

| Mode | Median total | Trips loaded/queried | Connections loaded | Trip queries | Index memory |
|---|---:|---:|---:|---:|---:|
| Frontier | 3,143 ms | 627 | 15,846 | 125 batched | 10.64 MB |
| Eager bulk | 4,539 ms | 5,189 | 148,097 | 3 batched | 18.08 MB |
| Legacy lazy | 3,737 ms | 4,019 | 67,406 | 4,019 individual | not estimated |

Frontier loading avoided 4,562 eager trips and 132,251 eager connections. Its
125 batches averaged 5.016 trips, had a maximum size of 49, and included 57
single-trip batches. Those frontier queries took approximately 279 ms in
total, so speculative loading was not added merely to increase batch size.
`unexpected_nonbulk_trip_queries` and `repeated_trip_fetch_attempts` were both
zero.

All three modes returned the same complete normalized response, with hash:

```text
b1955dbf4efacd2d48bcdec6330cb76dd3e38a86cb3af6a325948ce9d539a21a
```

The comparison includes trip IDs, stop IDs, departure and arrival times,
transfer counts, intermediate stops, reliability values, combined scores,
fallback levels, alternatives, and ordering. No approximate pruning, label
cap, reduced horizon, altered dominance rule, or reliability formula was
introduced.

The remaining representative costs are the initial departure preload at about
1.39 seconds and search/label CPU at about 1.12 seconds. Frontier query I/O is
now much smaller. Timings such as `frontier_trip_loading_ms` intentionally
overlap their query and indexing subcategories; use total time and
`search_cpu_excluding_frontier_io_ms` rather than summing every diagnostic
field. Results vary with the GTFS snapshot, PostgreSQL cache state, hardware,
and concurrent load.

The optimized behavior is covered by deterministic tests for route parity,
profile fallback parity, GTFS times beyond 24 hours, batching and
deduplication, empty trips, non-boardable departures, request isolation, hot
loop query prevention, timeout diagnostics, and API compatibility. Run:

```powershell
python -m pytest -m "not integration" -v
python -m pytest -m integration -s
```

### Routing cache architecture, cold-start optimization, and configuration

The routing cache work was completed in two stages: first, bounded shared caches
were added to reuse slowly changing transit data across requests; second, the
measured cold-cache work was moved into a configurable startup coordinator so
the first user request no longer pays the full daily-index construction cost.
Routing, Pareto dominance, A* queue semantics, reliability scoring,
reconstruction, and final ranking were not changed.

#### Application-scoped cache manager

FastAPI creates one `RoutingCacheManager` during application startup and injects
that same manager into route searches and reliability resolvers. It owns all
process-wide entries; there are no scattered unbounded module dictionaries.
The implementation uses standard-library `OrderedDict`, `RLock`, and `Event`
primitives to provide:

- bounded LRU eviction and per-cache TTLs;
- thread-safe lookup and publication;
- immutable statistics snapshots;
- hit, miss, load, eviction, and negative-hit counters;
- approximate process-cache memory reporting;
- explicit cache clearing for tests;
- explicit GTFS- and profile-version invalidation;
- per-key single-flight coordination without holding a cache lock during SQL;
- failure-safe publication: partially built values never enter a cache.

All externally reusable keys contain the data version that affects their value.
GTFS data uses the active `feed_info.feed_version`, with the imported feed date
range as a fallback. Reliability data uses the latest completed profile
aggregation `updated_at` value. Imports are transactional, so a failed or
partially completed import cannot publish a new active feed version. Old entries
may remain until TTL/LRU eviction, but a new version makes them unreachable.

The shared cache layers are:

| Layer | Key | Stored value and behavior |
| --- | --- | --- |
| Trip connections | `(gtfs_version, trip_id)` | Immutable, ordered complete trip chain; missing trips use a shorter negative TTL |
| Static GTFS snapshot | `gtfs_version` | Immutable stop metadata/coordinates and transfers grouped through the search index |
| Active service day | `(gtfs_version, service_date)` | Immutable active service-ID set using calendar rules and exceptions |
| Daily departures | `(gtfs_version, service_date)` | Immutable departures and parallel departure-time tuples grouped by stop for binary search |
| Reliability profiles | `(profile_version, route_id, direction_id, time_window)` | Raw exact profile and fallback parents; `minimum_samples` selection remains request-local |
| A* heuristic | `(gtfs_version, destination_stop_id, current_stop_id)` | Bounded Haversine travel-time estimate; request-local lookup remains first |
| Exact route response | All behavior-changing request fields plus GTFS/profile versions | Successful immutable result only; exact departure time and scoring inputs are never rounded |
| Warm-up completion | `(phase, gtfs_version, service_date)` | Prevents repeated successful SkyTrain/date warm-ups |

Trip lookup remains ordered as request-local cache, shared cache, bounded batch
query for remaining trip IDs, and publication into both caches. Empty trips are
remembered during the request and in the short-lived negative cache, so a known
empty trip is not repeatedly queried. Shared collections are tuples or mapping
proxies so callers cannot mutate data used by another request.

Bus trip loading remains frontier-driven. The daily index contains boardable
departure edges, not every complete trip chain. A* collects only trips reached
by its real frontier and continues to batch-load those chains. The shared cache
therefore does not revert to eager loading of the whole bus network.

#### Switching between previous and shared caching

API requests accept:

```json
{
  "cache_mode": "shared"
}
```

`shared` enables the new process-wide caches. `request` bypasses the shared
trip, daily, static, profile, heuristic, and response caches and reproduces the
previous request-local behavior while retaining frontier trip loading. The
benchmark exposes the same switch:

```powershell
python scripts/benchmark_astar.py --cache-mode request
python scripts/benchmark_astar.py --cache-mode shared
```

The code-wide default is intentionally one editable constant near the top of
`src/routing/cache.py`:

```python
DEFAULT_ROUTING_CACHE_MODE = "shared"  # "shared" or "request"
```

An explicit API value or benchmark argument overrides that default.

#### Daily departure index and GTFS service-day semantics

The daily index is built lazily or during startup for one
`(gtfs_version, service_date)` at a time. The database returns departures only
for services active on that date. Calendar additions and removals are included,
including services added solely through `calendar_dates`. Departures are grouped
by stop, deterministically sorted by departure time, trip, and stop sequence,
and published only after the entire immutable index is complete. Searches use
`bisect_left`/`bisect_right` over parallel `timedelta` tuples.

PostgreSQL intervals remain Python `timedelta` values throughout this path; they
are never cast to `datetime.time`. Departures such as `24:01:00` and `25:10:00`
therefore retain the correct GTFS service date and participate in binary search
without modulo-24 truncation.

Only three service dates are retained by default. Concurrent requests for the
same missing date wait on one per-key single-flight build. Different cache keys
do not share a global cache-build lock, and database I/O occurs outside the
cache-manager lock.

#### Cold-cache diagnosis

The first shared-cache implementation improved repeated requests but increased
the representative first request from roughly 2.8 seconds to 10.3 seconds.
Component-level diagnostics were added before changing behavior. The measured
cold A* request was:

| Cold component | Measured time |
| --- | ---: |
| GTFS version lookup | 24 ms |
| Static snapshot construction | 53 ms |
| Daily departure database query | **8,475 ms** |
| Daily departure grouping | 58 ms |
| Daily departure sorting | 135 ms |
| Total daily-index construction | **8,804 ms** |
| Frontier trip-connection queries | 217 ms |
| A* search | 1,303 ms |
| Ranking | 2 ms |
| Total request | **10,311 ms** |

The database query for a complete active service day--not Python grouping or
sorting--was the dominant regression. A serialized/prebuilt Python index artifact
was therefore not added: it would add format, atomic-write, and deployment
complexity while leaving the dominant database extraction problem to be solved
elsewhere. The prepared-artifact idea remains a later option if imports can
produce a safe versioned format directly.

#### Startup warm-up coordinator

`RoutingWarmupCoordinator` reuses the existing cache manager and the same
`ensure_static_snapshot` and `ensure_daily_index` builders used by request-time
fallback. It does not maintain a second cache system.

The essential startup phase resolves the active GTFS version, publishes the
immutable stop/transfer snapshot, caches today's active services, and--when
enabled--builds today's daily departure index. Predictable work is run with
`asyncio.to_thread`, so blocking PostgreSQL and CPU work never executes directly
on FastAPI's event loop.

After essential data is ready, the coordinator can preload active SkyTrain trip
chains. Tomorrow's index and SkyTrain trips can optionally run afterward in the
background. Tomorrow warming is disabled by default so it cannot compete with
live requests on a small Render database. Warm-up tasks are version/date
single-flight operations, log one completion or failure, accept a shutdown stop
request, and finish safely before owned database services are closed.

If startup warming fails, the failing phase is recorded, no partial object is
published, and readiness remains false. Calling the coordinator again can safely
retry. If the configured readiness timeout expires, the process becomes live,
warming continues in the protected task, and `/ready` remains false until the
minimum structures are available.

#### Selective SkyTrain preloading

SkyTrain is identified from GTFS metadata using `routes.route_type = 1`, the
GTFS subway/metro category. Names such as Expo, Millennium, and Canada Line are
not hardcoded. The selected trips are intersected with service IDs active for
the warmed Vancouver service date, so inactive rail trips are not cached and
calendar exceptions remain authoritative.

The active SkyTrain trip IDs are passed to `RequestTripConnectionLoader`, which
uses the normal bounded batch query and publishes ordered tuples under the
normal `(gtfs_version, trip_id)` key. SkyTrain coordinates, transfers, and
departures reuse the static and daily snapshots; no duplicate SkyTrain-specific
index exists. Bus trips continue to load from the live A* frontier.

For the measured feed, startup found 3 rail routes, 2,129 active trips, and
33,178 connections. The shallow incremental cache estimate was about 747 KB.
Those 2,129 entries fit within the normal 10,000-trip capacity, leaving room for
approximately 7,871 recently used bus trips, so a second protected rail cache
was not necessary.

#### Health, readiness, and observability

`/health` reports that the API process is alive. `/ready` separately reports
whether the minimum warmed data is available. Its non-sensitive response
includes GTFS version, essential/SkyTrain completion, background activity,
failure state, phase timings, rail route/trip/connection counts, rail memory,
and single-flight waits. Credentials, connection strings, and user route
queries are never exposed.

Profiled route diagnostics now include:

- GTFS version lookup time;
- static snapshot construction time;
- daily departure query, grouping, sorting, and total index time;
- SkyTrain and reliability warm-up time fields;
- request-local and shared trip-cache hits separately;
- negative trip-cache hits;
- daily, reliability, and heuristic hits/misses;
- first shared-request hits and misses;
- response-cache hit state;
- aggregate evictions, single-flight waits, and shallow cache memory.

Process startup-to-ready time and first user-request time are reported
separately. Moving 8-9 seconds into startup is not presented as eliminating the
work.

#### Configuration defaults

| Variable | Default | Purpose |
| --- | ---: | --- |
| `ROUTING_TRIP_CACHE_CAPACITY` | `10000` | Complete frontier/SkyTrain trip chains; tune from production memory measurements |
| `ROUTING_TRIP_CACHE_TTL_SECONDS` | `3600` | Positive trip, static, service-day, and daily-index lifetime |
| `ROUTING_NEGATIVE_TRIP_CACHE_TTL_SECONDS` | `60` | Missing or empty trip lifetime |
| `ROUTING_DAILY_INDEX_CAPACITY` | `3` | Service dates retained per worker |
| `ROUTING_HEURISTIC_CACHE_CAPACITY` | `50000` | Destination/current-stop Haversine entries |
| `ROUTING_PROFILE_CACHE_CAPACITY` | `20000` | Raw reliability cells and parent profiles |
| `ROUTING_RESPONSE_CACHE_ENABLED` | `true` | Enable exact completed-response caching |
| `ROUTING_RESPONSE_CACHE_CAPACITY` | `256` | Completed responses retained per worker |
| `ROUTING_RESPONSE_CACHE_TTL_SECONDS` | `60` | Completed-response lifetime |
| `CACHE_WARMUP_ENABLED` | `true` | Enable startup coordinator |
| `CACHE_WARMUP_BLOCK_READINESS` | `true` | Wait for essential warming before normal readiness |
| `CACHE_WARMUP_TODAY_INDEX` | `true` | Build today's measured-expensive daily index at startup |
| `CACHE_WARMUP_SKYTRAIN` | `true` | Preload active route-type-1 trip chains |
| `CACHE_WARMUP_TOMORROW_INDEX` | `false` | Optionally build tomorrow after essential readiness |
| `CACHE_WARMUP_TIMEOUT_SECONDS` | `30` | Maximum blocking readiness wait before warming continues asynchronously |

#### Measured cache and warm-up results

For the representative multi-leg A* route:

| Mode | Startup-to-ready | First request | Second request | Warm transit DB queries | Shallow shared memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous request-local caching | 34 ms | 2,818 ms | 2,816 ms | 117 | 0 |
| Shared caching, warming disabled | approximately 34 ms | 10,311 ms | 969 ms | 0 | 41.6 MB |
| Essential startup warming | 8,749 ms | 1,393 ms | 969 ms | 0 | 41.6 MB |
| Essential plus SkyTrain warming | 9,136 ms | 1,336 ms | 1,203 ms | 0 | 42.3 MB |

For a measured SkyTrain-only route from stop `8039` to `8058`:

| Mode | Startup-to-ready | First request | Second request |
| --- | ---: | ---: | ---: |
| Previous request-local caching | 34 ms | 2,969 ms | 2,794 ms |
| Essential warming | 9,081 ms | 1,314 ms | 641 ms |
| Essential plus SkyTrain warming | 9,431 ms | **1,095 ms** | **604 ms** |

An overnight route departing at `24:01:00` returned the exact trip at
`1 day, 0:01:00`; its first post-readiness request took 106 ms and the second
took 59 ms. Cold and warm route signatures were identical in every measured
case. The first user-visible request is therefore substantially faster while
warm performance is retained, with the startup-to-ready cost reported openly.

#### Verification and limitations

Tests cover bounded LRU eviction, trip reuse, batching, negative caching,
version isolation, raw-profile reuse with different `minimum_samples`, exact
response keys, GTFS times beyond 24 hours, route-type-1 rail selection, active
service filtering, SkyTrain trip publication, concurrent daily/SkyTrain
single-flight builds, failed-build publication safety, background shutdown,
readiness state, and cold/warm equality. At implementation time the focused
suite passed 44 tests. The complete suite passed 158 tests with one pre-existing
API-default mismatch: the schema currently defaults to `astar` while that test
expects `mc_raptor`; the caching work did not change the algorithm default.

The cache memory figures are deliberately labeled shallow estimates. Python
object dictionaries and referenced strings mean real retained memory is higher,
so capacities must be checked against production RSS on Render. Every
Uvicorn/Gunicorn worker has an independent cache, warms independently after a
restart, and pays its own roughly nine-second startup build. Redis may later be
useful for immutable trip chains and raw profiles when multi-worker duplication
justifies serialization and network overhead; it would not by itself remove the
daily departure extraction cost.

Angle-only speculative prefetching remains intentionally excluded. A future
experiment must use scheduled downstream arrival plus A*'s actual `f = g + h`
frontier priority, remain strictly capped, and demonstrate useful prefetch hit
rates before being enabled.
