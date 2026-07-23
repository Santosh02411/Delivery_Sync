# Offline-First Delivery Status Sync

A delivery status tracking system built to work fully offline and sync
automatically once connectivity returns — designed for delivery agents
working in low-network areas (basements, elevators, rural zones, dense
buildings, and rural regions with unreliable signal).

## Problem It Solves
Delivery agents lose connectivity constantly during their routes. Most
delivery apps either fail silently or block the agent from updating status
until signal returns, causing delays and lost or duplicate updates. This
project solves that with local-first storage on the client and a
conflict-aware sync engine on the backend.

## Key Features
- Works with **zero internet connection** — agents can view and update
  deliveries fully offline
- Automatic background sync — retries and reconciles pending changes once
  connectivity returns, with no manual action needed
- **Conflict resolution** — if the same delivery is updated in two places
  while offline, a last-write-wins strategy (based on timestamps) resolves
  it automatically and consistently
- Dispatcher dashboard with status filtering, to monitor all deliveries
  across agents in real time
- Full delivery lifecycle: create, update status, delete — both locally and
  synced to the server

## Tech Stack
| Layer | Technology | Why |
|---|---|---|
| Frontend | React + Vite | Modern, fast dev experience |
| Offline Storage | IndexedDB | Structured local storage, works fully offline |
| Backend | FastAPI | Async, auto-documented API, ideal for this API-only use case |
| Database | SQLite | Zero-setup, keeps focus on sync logic over DB administration |
| Sync Logic | Custom-built | Last-write-wins conflict resolution, hand-written for full understanding and control |

## Architecture (High Level)
```
[Agent's Browser]
   React UI → IndexedDB (offline-first local storage)
        ↓ (when online)
   Sync Engine → POST /sync
        ↓
[FastAPI Backend] → Conflict Resolution → SQLite (source of truth)
        ↓
[Dispatcher Dashboard] ← GET /deliveries
```

Full architecture details, data model, and design reasoning are in
[`docs/TECHNICAL_ARCHITECTURE.md`](docs/TECHNICAL_ARCHITECTURE.md).

## How to Run Locally

This project has two parts that must run at the same time, in two separate
terminals.

**1. Backend:**
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```
Runs at `http://127.0.0.1:8000` — visit `/docs` for the interactive API
documentation (Swagger UI).

**2. Frontend** (in a separate terminal):
```bash
cd frontend
npm install
npm run dev
```
Runs at `http://localhost:3000`.

Both must be running simultaneously for the app to work correctly.

## Project Structure
```
delivery-sync-project/
├── backend/       # FastAPI app (models, routes, sync/conflict logic)
├── frontend/      # React app (offline storage, sync engine, UI)
├── docs/          # Full project documentation (see below)
└── README.md      # This file
```

## Full Documentation
This project is documented in more depth than a typical student project,
intentionally — the `docs/` folder includes:
- [`PROJECT_REQUIREMENTS.md`](docs/PROJECT_REQUIREMENTS.md) — functional and non-functional requirements
- [`TECHNICAL_ARCHITECTURE.md`](docs/TECHNICAL_ARCHITECTURE.md) — architecture, data model, conflict resolution strategy
- [`SECURITY_AND_ACCESS.md`](docs/SECURITY_AND_ACCESS.md) — auth model and known v1 limitations
- [`DESIGN_AND_FRONTEND_SPECS.md`](docs/DESIGN_AND_FRONTEND_SPECS.md) — UI/UX specs
- [`FEATURE_TICKET_LIST.md`](docs/FEATURE_TICKET_LIST.md) — feature tracking by phase
- [`PROJECT_WORKFLOW.md`](docs/PROJECT_WORKFLOW.md) — a detailed, phase-by-phase build log covering every real bug encountered (a CORS misconfiguration, a timezone comparison bug, and more) and exactly how each was diagnosed and fixed
- [`MEMORY.md`](docs/MEMORY.md) — key design decisions and the reasoning behind them

## Known Limitations (v1)
- Last-write-wins conflict resolution can silently overwrite near-simultaneous
  edits — a future iteration could explore field-level merging
- No authentication/authorization yet beyond a placeholder agent ID
- SQLite is used for simplicity — a production version would move to
  PostgreSQL/MySQL

## Author
Built by Santy as a portfolio project targeting Python Full Stack, Software
Developer, and Backend Developer roles.
