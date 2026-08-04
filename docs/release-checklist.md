# Portfolio release checklist

No item in this checklist authorizes a tag, push, deployment, or GitHub release.

## Source and branch

- [ ] Confirm the release commit is on `main`.
- [ ] Confirm Render backend and frontend both target `main`.
- [ ] Confirm no development/historical branch is an automatic deployment source.
- [ ] Review `git status` and exclude local secrets, generated feeds, snapshots,
      virtual environments, and build output.

## Runtime and dependencies

- [ ] Confirm Python 3.12.11 in `.python-version`, `pyproject.toml`, CI, and Render.
- [ ] Confirm Node 22.16.0 in `.nvmrc`, package engines, CI, and Render.
- [ ] Run Python dependency compatibility checks.
- [ ] Run `npm audit --omit=dev --audit-level=high`.

## Verification

- [ ] Run `python -m pytest`.
- [ ] Run `python -m pytest -m integration -rs` against populated PostgreSQL.
- [ ] Run Ruff lint and format checks.
- [ ] Run frontend tests, ESLint, Prettier, and production build.
- [ ] Validate `render.yaml` and `docker-compose.yml`.
- [ ] Check every Markdown link.

## Feed and snapshot

- [ ] Download and dry-run the current static GTFS feed.
- [ ] Confirm data attribution and current TransLink terms.
- [ ] Import the intended feed and aggregate current reliability data.
- [ ] Build and validate a format-3 snapshot.
- [ ] Confirm earliest/latest usable service dates and warning window.
- [ ] Confirm `/ready` returns `200` and identifies the loaded snapshot.
- [ ] Confirm an expired snapshot returns `503`.
- [ ] Reproduce at least one API example and dated benchmark.

## Environment and deployment

- [ ] Set backend `API_CORS_ORIGINS` to the real frontend origin.
- [ ] Set frontend `VITE_API_BASE_URL` to the real backend origin.
- [ ] Configure build-time `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and
      `DB_PASSWORD` as Render secrets.
- [ ] Confirm snapshot-required and development-fallback flags match deployment docs.
- [ ] Confirm no secret appears in logs, configuration, or frontend bundles.

## Live verification after an authorized deployment

- [ ] Verify backend `/health` returns `200`.
- [ ] Verify backend `/ready` returns `200` and `snapshot_loaded: true`.
- [ ] Search for a real stop from the frontend.
- [ ] Route a direct journey and a transfer journey.
- [ ] Verify Dijkstra and A* requests.
- [ ] Verify alternatives and map/card selection.
- [ ] Verify timeout, planner-unavailable, no-route, and expired-feed messages.
- [ ] Check browser console, CORS, Google Fonts fallback, and tile-error behavior.

## Release record

- [ ] Finalize `CHANGELOG.md` date and release notes.
- [ ] Obtain explicit authorization to tag, push, deploy, and create the release.
- [ ] Create the `v1.0.0` tag and GitHub release only after authorization.
