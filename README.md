# Delivery Sync

A complete, multi-tenant delivery tracking platform: offline-first status
updates for delivery agents, a full dispatcher/admin operations console,
and a genuine customer-facing portal — order tracking, live notifications,
and feedback, with no backend or terminal access required to use it.

## What This Actually Is

This started as an offline-sync exercise and grew into a full logistics
platform with three separate, real user experiences:

1. **Agents** — update delivery status (works fully offline, syncs when
   back online), scan packages, see their route, message dispatch.
2. **Dispatchers / Admins** — assign deliveries (single or bulk CSV
   import), track everything on a live dashboard, manage their
   organization's users, export data, message agents.
3. **Customers** — sign up, see every order linked to their account
   (across any organization using this platform), get real in-app
   notifications the moment a status changes, view proof of delivery,
   and leave a rating — all inside the product itself.

Multi-tenant from the ground up: any number of separate delivery
companies ("organizations") can use the same running instance without
ever seeing each other's data.

## How to Access Each Portal

There is **one single login page** for everyone — an **"Account Type"**
dropdown at the top of both the Login and Signup forms switches between
**Staff (Agent / Dispatcher / Admin)** and **Customer**, which swaps the
form fields (username vs. email) and which account actually gets created
or logged into.

- **Staff or customer, login or signup:** just open the app — you land
  directly on the login page, pick your account type from the dropdown,
  and go.
- **Public order tracking (no account at all):** `?track=<delivery-id>` —
  shareable with anyone, works with zero login.
- Staff and customer sessions are stored completely separately in the
  browser. If both happen to be logged in at once (e.g. you tested both),
  the staff session takes priority on load — log out of staff to drop
  back to the login page and access the customer account instead.

## How to Run Locally

Two servers, two terminals — both must run at the same time.

**Backend:**

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

Runs at `http://127.0.0.1:8000` — visit `/docs` for the interactive API
reference.

**Frontend** (separate terminal):

```bash
cd frontend
npm install
npm run dev
```

Runs at `http://localhost:3500`.

> **If you're pulling a fresh copy of this project after previously
> running an older version:** delete `backend/database.db` before
> starting the backend. The schema has grown a lot across development
> (organizations, customers, notifications, feedback, and more) and an
> old database file predates those tables.

## Running the Test Suite

```bash
cd backend
pip install -r requirements.txt
pytest -v
```

26 tests covering auth (staff + customer), the public tracking page,
security headers, and rate limiting — each test runs against its own
isolated temp SQLite database, never the real `database.db`. See
`backend/tests/`. A GitHub Actions workflow
(`.github/workflows/ci.yml`) runs this suite, a frontend build check,
and a Docker image build check automatically on every push and pull
request.

## Getting Started — A Full Walkthrough

1. Open the app, choose **Staff Login**, sign up. The first person to
   sign up for a new company **creates an organization** and
   automatically becomes its **admin** — this generates an invite code.
2. Sign up a second staff account, this time **joining** that
   organization with the invite code, as an **agent**.
3. As the admin/dispatcher, go to the **Dashboard** and assign a
   delivery to that agent — optionally fill in a customer email so it
   links to a real customer account later.
4. Open a separate tab/session, choose **Track My Orders (Customer)**,
   sign up using the _same_ email you entered above — the order
   auto-links to their account immediately (even retroactively, if they
   sign up after the order already existed).
5. As the agent, update the delivery's status — watch the customer's
   dashboard pick up a live in-app notification.
6. Mark it **Delivered** (capturing a signature or photo) — the customer
   can now leave a star rating, right in their dashboard.

## Key Features

- **Offline-first agent app** — IndexedDB-backed local storage with a
  conflict-resolving sync engine; works with zero connectivity
- **Multi-tenant** — organizations, invite-code onboarding, full data
  isolation, verified with real cross-org isolation tests
- **Admin panel** — manage staff accounts, deactivate/reactivate, reset
  passwords
- **Real customer accounts** — not just a tracking link: full dashboard,
  in-app notifications, order history, feedback
- **Route optimization** — zone grouping + nearest-neighbor ordering
  (no paid maps API)
- **Bulk CSV import** — with per-row validation and clear error reporting
- **Proof of delivery** — signature capture or photo, required to mark
  Delivered
- **Barcode/QR scanning** — native browser API, no extra libraries
- **Dispatcher ↔ agent messaging** — per-delivery chat thread
- **CSV export**, **rate limiting**, **PWA support**, **light/dark theme**

## Tech Stack

| Layer           | Technology                                        |
| --------------- | ------------------------------------------------- |
| Frontend        | React + Vite                                      |
| Offline Storage | IndexedDB (per-user scoped)                       |
| Backend         | FastAPI                                           |
| Database        | SQLite                                            |
| Auth            | JWT, separate token types for staff vs. customers |
| Rate Limiting   | slowapi (in-memory, Redis-ready)                  |

## Full Documentation

The `docs/` folder is more thorough than most student projects
intentionally:

- [`TECHNICAL_ARCHITECTURE.md`](docs/TECHNICAL_ARCHITECTURE.md) — architecture and data model
- [`SECURITY_AND_ACCESS.md`](docs/SECURITY_AND_ACCESS.md) — auth model, known limitations
- [`FEATURE_LOG.md`](docs/FEATURE_LOG.md) — every feature: what was missing, why it was built, what it does
- [`PROJECT_WORKFLOW.md`](docs/PROJECT_WORKFLOW.md) — every real bug hit during development and how it was diagnosed/fixed
- [`PROJECT_REQUIREMENTS.md`](docs/PROJECT_REQUIREMENTS.md) — requirements
- [`FEATURE_TICKET_LIST.md`](docs/FEATURE_TICKET_LIST.md) — feature tracking by phase

## Known, Disclosed Limitations

- Password reset is admin-set for staff (no email service configured by
  default — console-logged instead, real SMTP works via env vars)
- Email/SMS notifications default to console-log; real delivery needs
  SMTP/Twilio credentials via environment variables
- Rate limiter is in-memory by default; set `REDIS_URL` for a real
  multi-server deployment
- Barcode/QR scanning uses the browser's native `BarcodeDetector` API,
  currently Chrome/Edge only (not Firefox/Safari)

## Author

Built by Santy as a portfolio project targeting Python Full Stack,
Software Developer, and Backend Developer roles.
