# Vancouver Transit Planner frontend

This React client demonstrates the planner's core differentiator: routes are
ranked using both scheduled travel time and historical reliability.

## Local development

Start FastAPI from the repository root:

```powershell
python -m uvicorn src.api.main:app --reload
```

In another terminal, install and start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. The frontend defaults to the FastAPI service at
`http://127.0.0.1:8000`.

To use another API URL, copy `.env.example` to `.env` and change:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

FastAPI's existing CORS configuration permits explicit local origins on ports
3000 and 5173. For another frontend origin, set the backend's comma-separated
`API_CORS_ORIGINS` variable.

## Validation

```powershell
npm run test
npm run build
```

Tests mock the API and require no PostgreSQL database or internet access. The
production build is written to `frontend/dist/`.

## Current map limitation

The API returns coordinates for stops but not complete route shapes. The map
therefore shows origin, destination, and available leg endpoints without
drawing a misleading transit path.
