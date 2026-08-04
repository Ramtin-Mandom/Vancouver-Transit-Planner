# Development guide

## Supported runtimes

- Python 3.12.11 (`.python-version`, `pyproject.toml`, Render, and CI).
- Node.js 22.16.0 (`frontend/.nvmrc`, package engines, Render, and CI).
- PostgreSQL 17 Alpine for local Compose; CI integration uses PostgreSQL 16.

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
cd frontend
npm ci
```

Production Python dependencies live in `requirements.txt`. Test and lint tools
live in `requirements-dev.txt`, so Render does not install them.

## Configuration

Copy `.env.example` to `.env`. Never commit credentials. The database pipeline
requires `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`.
Production snapshot startup requires `ROUTING_SNAPSHOT_PATH` and
`API_CORS_ORIGINS`; Render supplies the remaining non-secret snapshot flags.

`VITE_API_BASE_URL` is a frontend build variable and belongs in the frontend
environment, not the backend `.env`.

## Quality commands

Backend:

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

Database integration tests require a populated GTFS database:

```powershell
python -m pytest -m integration -rs
```

Frontend:

```powershell
cd frontend
npm test
npm run lint
npm run format:check
npm run build
npm run audit:production
```

GitHub Actions runs equivalent backend and frontend jobs and provisions a
separate PostgreSQL service for integration tests.

## Local services

```powershell
docker compose up -d
docker compose ps
docker compose down
```

The API and frontend development commands are in the root README. Use `/ready`
when testing routing availability; `/health` only tests process liveness.

## Branch and release policy

`main` is the only deployment branch. Feature, benchmark, debug, and historical
branches must not be automatic production sources. Do not push, tag, deploy, or
create a release without explicit authorization.

Experimental routers and caches are intentional. A cleanup must not delete one
because it is absent from the production schema. Routing-module moves require a
focused test before and after each separate commit.

## Frontend runtime dependencies

Leaflet uses OpenStreetMap tiles over the network. Google Fonts may be requested
by CSS. Keep system fallback fonts, accessible focus indicators, form labels,
and the map tile-error state working when those services are unavailable.

## Documentation maintenance

Examples must be derived from current Pydantic schemas and reproduced where
possible. Benchmark numbers require a dated command and environment. Do not
copy historical README claims into current docs without verifying them.
