# Data pipeline

## Data flow

Static GTFS is downloaded, validated, imported into PostgreSQL, combined with
aggregated reliability data, and serialized into a production routing snapshot.
The full feed and generated arrays are intentionally ignored by Git.

## Local PostgreSQL

Copy `.env.example` to `.env`, replace `DB_PASSWORD`, and start PostgreSQL 17:

```powershell
docker compose up -d
docker compose ps
```

The Compose service maps `${DB_PORT:-5432}`, uses the `DB_NAME`, `DB_USER`, and
`DB_PASSWORD` values, persists data in `transit_postgres_data`, and includes a
`pg_isready` health check.

Stop without deleting data:

```powershell
docker compose down
```

`docker compose down -v` also removes the named database volume and should be
used only when a destructive reset is intended.

## GTFS refresh

Download the current published archive:

```powershell
python -m scripts.download_gtfs --force
```

Validate without a database write:

```powershell
python -m src.data_ingestion.cli --dry-run
```

Create the schema for a new database and import:

```powershell
psql -h localhost -U transit -d vancouver_transit -f database/schema.sql
python -m src.data_ingestion.cli --replace
```

`--replace` truncates and reloads the known transit tables transactionally. Read
the command output before using it against a non-development database.

## Reliability pipeline

The optional GTFS-Realtime collector requires `TRANSLINK_API_KEY`. Parsed trip
updates become delay observations. Aggregation produces exact
route/direction/time-window profiles and broader fallback rows with sample and
distinct-service-date counts.

Profile selection order is:

1. Exact route, direction, and time-window profile.
2. Route and direction profile.
3. Route profile.
4. Network profile.
5. Explicit conservative default when no sampled profile exists.

An exact profile can be marked under-sampled. The selected level and
`insufficient_data` state are serialized; the frontend does not infer them.

## Snapshot build

```powershell
python -m scripts.build_routing_snapshot --output data/routing_snapshot
python -m scripts.validate_routing_snapshot data/routing_snapshot
```

The builder uses a server-side cursor for timetable connections and records
source version, service range, counts, duration, size, peak RSS, transfer index,
reliability arrays, and geographic-heuristic validation in the manifest.

The writer emits snapshot format 3. The current loader safely accepts format 2
and 3. Unsupported versions, corrupt arrays, invalid time ordering, or unsafe
metadata fail validation rather than reaching production.

## Feed expiration

The snapshot derives earliest and latest usable service dates from calendars and
exceptions. During the final 30 days `/ready` includes a warning. After the last
usable date, `/ready` returns `503` and routing reports feed expiration instead
of “no routes.”

Refresh procedure:

1. Download the new feed.
2. Dry-run validation.
3. Import with the intended replacement database.
4. Recompute reliability profiles when current observations are available.
5. Rebuild and validate the snapshot.
6. Run backend tests and one real route request within the new service range.
7. Deploy only through `main` after explicit authorization.

See [data attribution](../data/README.md).
