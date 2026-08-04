# Deployment

## Branch policy

`main` is the single production and deployment branch. `render.yaml` explicitly
sets both services to `branch: main`. Other branches are development or
historical work and must not be configured for automatic deployment.

## Render services

The Blueprint defines:

1. A Python 3.12.11 FastAPI web service.
2. A Node 22.16.0 static frontend build.

Backend build command:

```bash
bash scripts/render_build.sh
```

Backend start command:

```bash
python -m uvicorn src.api.main:app --host 0.0.0.0 --port $PORT --workers 1
```

Frontend build and publish settings:

```text
Root directory: frontend
Build command: npm ci && npm run build
Publish directory: ./dist
```

No live URL is recorded because Render assigns it outside the repository.

## Required Render values

The Blueprint cannot derive these values before service creation:

- `API_CORS_ORIGINS`: deployed frontend origin, without invented wildcards.
- `VITE_API_BASE_URL`: deployed backend origin.
- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`: a PostgreSQL source
  used by the backend build command to generate the snapshot.

The database credentials are build-time inputs. The running snapshot API does
not use PostgreSQL for requests.

Configured non-secret values:

```text
ROUTING_SNAPSHOT_PATH=data/routing_snapshot
ROUTING_SNAPSHOT_REQUIRED=true
ROUTING_SNAPSHOT_DEVELOPMENT_FALLBACK=false
CACHE_WARMUP_ENABLED=false
ROUTING_SNAPSHOT_BUILD_MAX_RSS_MB=450
```

## Health checks

- `GET /health`: process liveness; `200` while FastAPI is running.
- `GET /ready`: routing readiness; `200` only with an active compatible and
  unexpired planner, otherwise `503`.

Render checks `/ready` because accepting HTTP is insufficient if routing cannot
serve requests.

## Deployment verification

After an authorized deployment:

```bash
curl https://BACKEND.example/health
curl https://BACKEND.example/ready
curl "https://BACKEND.example/stops/search?query=Gran&limit=5"
```

Verify that `/ready` reports `snapshot_loaded: true`, a supported snapshot
version, current service range, and no expiration error. Then make one real route
request from the deployed frontend and confirm the browser has no CORS failure.

## Runtime external services

The frontend requests Google Fonts and OpenStreetMap tiles at runtime. Font
failure falls back to the configured system font stack. Tile failure produces a
map message; it does not stop route cards from rendering.

## Release safety

Do not deploy from `debug`, `final-changes`, or another development branch. Do
not place secrets or assigned service URLs in committed configuration. Consult
the [release checklist](release-checklist.md) before an authorized release.
