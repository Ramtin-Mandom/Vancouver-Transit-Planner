# Changelog

All notable project changes are documented here. The project follows semantic
versioning once a release is explicitly created.

## [Unreleased]

- Prepared portfolio documentation and release assets.
- Added CI coverage for backend, frontend, and PostgreSQL integration tests.
- Removed generated full-feed data from version control in favor of a
  reproducible download/import workflow.

## [1.0.0] - Draft

### Added

- Production FastAPI snapshot planner with Dijkstra and validated A* contracts.
- React route-planning interface with readiness-aware errors and selectable map
  alternatives.
- Versioned, memory-mapped snapshot build and validation pipeline.
- Reliability fallback profiles with actual selected-level metadata.
- Feed-expiration readiness protection and explicit routing timeouts.
- PostgreSQL ingestion, GTFS-Realtime observation, reliability aggregation, and
  opt-in integration testing.
- Retained experimental database routers, MC-RAPTOR, and cache strategies for
  comparative testing.

### Operations

- Render Blueprint deploys backend and frontend from `main` only.
- Production request handling is database-free after snapshot loading.
- Python 3.12.11 and Node 22.16.0 are pinned across development, CI, and Render.

No `v1.0.0` tag or release has been created.
