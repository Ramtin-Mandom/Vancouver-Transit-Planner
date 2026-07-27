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

```powershell
psql -d vancouver_transit -f database/migrations/001_reliability_data.sql
```

The migration preserves imported GTFS data. It adds `stop_sequence` to existing
delay observations, corrects snapshot uniqueness, and creates
`route_reliability`. Review the documented stop-sequence backfill before
running it, especially if delay observations already exist.

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

Build route/stop reliability profiles:

```powershell
python -m src.reliability.cli aggregate --minimum-samples 20
```

Aggregation uses only the latest observation for each
trip/stop/sequence/service-date combination, preventing frequently observed
trips from receiving extra statistical weight. A vehicle is considered on time
when its delay is no more than five minutes (`300` seconds).

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
  --simulations 1000 `
  --seed 42 `
  --max-extra-minutes 30 `
  --minimum-samples 20
```

Simulation uses a deterministic seed and a normal approximation based on each
selected profile's mean and standard deviation. Sampled delays are clamped
between 15 minutes early and two hours late. When no profile has enough data,
the result is explicitly marked insufficient and uses a conservative
10-minute mean and 10-minute standard-deviation fallback.
The reported on-time-arrival probability means arrival no more than ten minutes
after the final scheduled arrival.

Alternatives are ranked with:

```text
100 × (0.70 × completion probability
       + 0.30 × fastest scheduled duration / candidate scheduled duration)
```

Scores are clamped to `0–100`; ties prefer completion probability, scheduled
arrival, fewer transfers, and finally a stable itinerary identifier.

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
