# Repository organization

## Current layout

```text
.
|-- .github/workflows/ci.yml       CI and PostgreSQL integration verification
|-- data/README.md                 data policy, provenance, and attribution
|-- database/                      schema and migrations
|-- docs/                          architecture and operational notes
|-- frontend/                      React application and frontend tooling
|-- scripts/                       executable build, validation, and benchmarks
|   `-- sql/diagnostics.sql        read-only diagnostic SQL
|-- src/api/                       public FastAPI service
|-- src/data_ingestion/            GTFS validation and PostgreSQL loading
|-- src/reliability/               observations and fallback profiles
|-- src/routing/                   production and experimental routers/caches
`-- tests/                         unit, API, routing, and opt-in integration tests
```

Before this cleanup, test dependencies were installed in production, Python
configuration was split across `pytest.ini` and ad-hoc files, diagnostic SQL
lived beside schema assets, personal SQLTools connection details were tracked,
and about 128 MB of raw/extracted GTFS data was versioned. The current layout
centralizes Python tooling in `pyproject.toml`, separates production and
development requirements, gives diagnostics and generated data explicit homes,
and reproduces full data through `scripts.download_gtfs`.

## Routing module review

The production snapshot implementation and experimental database-backed models
remain intentionally side by side under `src/routing`. Their public imports and
all cache strategies are unchanged.

The five requested large modules were reviewed but not split during repository
cleanup:

- `reliable.py` and `planner.py` coordinate labels, caches, diagnostics, and
  compatibility behavior across multiple routers; moving one concern would
  create circular-import or facade risk.
- `database.py` is a cohesive PostgreSQL catalog/repository boundary despite its
  size.
- `snapshot.py` couples serialization compatibility to `SnapshotPlanner`; a
  future extraction should be versioned and benchmarked as routing work.
- `mc_raptor.py` is a self-contained experimental implementation and is clearer
  as one benchmarkable unit.

Formatting and import cleanup were applied, but no routing model, cache method,
public import, or algorithm was removed. Any future module split should be its
own commit with focused routing tests before and after the move.

## Integration tests

Normal `pytest` excludes tests marked `integration`. GitHub Actions has a
separate `database-integration` job that starts PostgreSQL 16, downloads the
current official static feed, creates the schema, imports the feed, and runs
`pytest -m integration`. Locally, run the same marker only after configuring a
populated database through the five `DB_*` variables.
