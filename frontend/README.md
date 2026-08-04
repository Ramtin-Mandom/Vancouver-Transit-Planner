# Frontend

React, TypeScript, Vite, and Leaflet client for the snapshot routing API.

## Local development

Start the ready snapshot API from the repository root, then:

```powershell
cd frontend
npm ci
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm run dev
```

Open `http://127.0.0.1:5173`. The header reports connected only when `/ready`
confirms that routing is available.

## Commands

```powershell
npm test
npm run lint
npm run format:check
npm run build
npm run audit:production
```

Tests mock the API and do not require PostgreSQL or internet access. The
production build is written to `dist/`.

## Map behavior

The API returns ordered leg stops but not full transit shape geometry. The map
therefore draws a separate stop-to-stop line for each alternative rather than
claiming a street-accurate path. Colors and selection stay synchronized with
route cards. OpenStreetMap tile failures display a fallback message, while
missing stop coordinates omit only the unavailable geometry.

See the root [development guide](../docs/development.md) for supported runtimes,
backend setup, and release policy.
