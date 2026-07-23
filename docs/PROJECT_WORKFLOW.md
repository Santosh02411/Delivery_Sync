# Project Workflow — Detailed Build Log

This document is a detailed, phase-by-phase account of how this project was
built: what was built in each phase, and — importantly — every real error,
bug, or mistake encountered along the way, which file it was in, why it
happened, and exactly how it was fixed. This is intentionally detailed and
written so it can be read on its own to understand the project deeply enough
to explain it confidently in an interview, not just to skim.

---

## Phase 1 — Backend Skeleton

**Goal:** Set up the FastAPI backend with a `DeliveryRecord` model and basic
CRUD (create, read, update, list) endpoints, backed by SQLite.

**What was built:**
- `backend/app/db/session.py` — SQLAlchemy engine + session setup for SQLite
- `backend/app/models/delivery.py` — the `DeliveryRecord` model, defined
  twice on purpose: once as `DeliveryRecordDB` (the actual database table,
  using SQLAlchemy) and once as several Pydantic schemas
  (`DeliveryRecordCreate`, `DeliveryRecordUpdate`, `DeliveryRecordOut`) that
  define what shape of data the API accepts and returns. Keeping these
  separate is a deliberate design choice: the database structure can evolve
  independently of what the API exposes to clients.
- `backend/app/routes/deliveries.py` — the actual endpoints:
  `POST /deliveries/` (create), `PATCH /deliveries/{id}` (update),
  `GET /deliveries/` (list all), `GET /deliveries/{id}` (get one)
- `backend/main.py` — the FastAPI app entry point, which creates the database
  tables on startup and wires the routes into the app

**Errors / issues faced in this phase:** None — this phase was tested
immediately using FastAPI's `TestClient` (create → list → update, all
verified working) before being handed over, so no bugs surfaced here later.

---

## Phase 2 — Offline-Capable Frontend

**Goal:** Build the React frontend, with an Agent view that saves data to
IndexedDB first (so it works fully offline), and a Dispatcher view that
reads directly from the backend.

**What was built:**
- `frontend/src/services/indexedDb.js` — a hand-written wrapper around the
  browser's raw IndexedDB API (no external library), with functions to save
  a record locally, get all local records, get only unsynced ("pending")
  records, and mark a record as synced.
- `frontend/src/services/api.js` — functions that call the FastAPI backend
  over HTTP (create, update, list, and later, sync).
- `frontend/src/hooks/useConnectivity.js` — a small React hook that tracks
  whether the browser is online or offline, using the browser's built-in
  `online`/`offline` events.
- Components: `ConnectivityBanner`, `SyncStatusBadge`, `DeliveryStatusUpdater`,
  `AgentDeliveryList` (the main agent-facing view), `DispatcherTable`.
- `App.jsx` — ties the Agent and Dispatcher views together with a simple
  toggle.
- Build tool: **Vite** was chosen over Create React App, since CRA is
  deprecated and Vite is the current standard, faster to start, and doesn't
  change how React itself is written.

### Error #1 — JSX files with the wrong extension

**What happened:** The very first time the frontend was run with
`npm run dev`, Vite failed with:
```
The JSX syntax extension is not currently enabled
src/App.js:10:4: <div>
```

**Why it happened:** `App.js` and `index.js` were originally created with a
plain `.js` extension, but they contained JSX syntax (things like `<div>`,
`<App />`). Vite's underlying bundler (esbuild) only automatically enables
JSX parsing for files ending in `.jsx` (or `.tsx`) — a `.js` file is assumed
to be plain JavaScript with no JSX inside it, so esbuild refuses to parse
the JSX and throws this error instead of guessing.

**File affected:** `frontend/src/App.js`, `frontend/src/index.js`

**How it was fixed:** Both files were renamed to `App.jsx` and `index.jsx`.
The one place that referenced the old filename — the `<script>` tag in
`frontend/index.html` pointing to `/src/index.js` — was updated to point to
`/src/index.jsx` instead.

**A follow-up mistake:** After sharing the renamed files, the same error
still appeared. This was because the *old* `App.js`/`index.js` files were
never actually deleted from the project folder before the new `.jsx` files
were added — both versions existed side by side. Vite's default module
resolution checks for a `.js` file before a `.jsx` file with the same name,
so it kept loading the old, broken `App.js`. The real fix required deleting
the entire old `frontend` folder and replacing it cleanly, not just adding
new files on top of old ones — a good general lesson: when replacing files
with a different extension, the old file must be explicitly removed, not
left alongside the new one.

---

## Phase 3 — Sync Engine with Conflict Resolution

**Goal:** Build the piece that actually pushes offline-saved records to the
backend once connectivity returns, and resolves conflicts if the same record
was changed in two places.

**What was built:**
- `backend/app/services/conflict_resolver.py` — the core conflict resolution
  logic: last-write-wins based on the `updated_at` timestamp. If an incoming
  record's timestamp is newer than what the server already has, the incoming
  change wins; otherwise, the server's existing version is kept.
- `backend/app/routes/sync.py` — the `POST /sync` endpoint, which accepts a
  batch of records from the client and applies the conflict resolution logic
  to each one, returning the final resolved version of each record.
- `frontend/src/services/syncEngine.js` — the client-side piece: `runSync()`
  gathers all pending (unsynced) local records and sends them to `/sync`,
  with retry logic (up to 3 attempts) if the request fails; `startAutoSync()`
  wires this up to run automatically.

This phase surfaced two real bugs — both are genuinely useful to describe in
an interview, since they're common, realistic problems in real sync systems,
not contrived ones.

### Error #2 — CORS misconfiguration (wildcard origin + credentials)

**What happened:** After building the sync engine, clicking "Sync Now" in
the browser failed with a vague `Sync failed: Failed to fetch` error, even
though the backend was confirmed to be running (its Swagger UI at
`/docs` loaded fine).

**Why it happened:** In `backend/main.py`, the CORS middleware (which
controls which frontend origins are allowed to call the API) was configured
with both `allow_origins=["*"]` (allow requests from any origin) **and**
`allow_credentials=True` (allow cookies/credentials) at the same time.
Browsers explicitly reject this exact combination for security reasons —
per the Fetch spec, a server cannot say "anyone can call me" and "also send
me your credentials" simultaneously, since that would be a security hole.
When a browser detects this combination in a response, it blocks the entire
response silently, which shows up in JavaScript as a generic, unhelpful
"Failed to fetch" error rather than a clear CORS message — making this
particularly tricky to diagnose without knowing this specific browser rule.

**File affected:** `backend/main.py`

**How it was fixed:** Since this project doesn't use cookies or
credential-based auth at all, `allow_credentials` was changed from `True` to
`False`. This makes the `allow_origins=["*"]` (allow-all) setting valid
again, since the invalid combination no longer exists. Verified afterward by
simulating a browser's CORS "preflight" request directly against the backend
and confirming the response headers were now consistent
(`Access-Control-Allow-Origin: *` with no conflicting credentials header).

### Error #3 — Comparing timezone-aware and timezone-naive datetimes

**What happened:** After the CORS fix, syncing worked for some records but
failed for others (and eventually for all new records), with the backend
terminal showing:
```
TypeError: can't compare offset-naive and offset-aware datetimes
```
at the exact line in `conflict_resolver.py` that compares timestamps to
decide which version of a record should win.

**Why it happened:** JavaScript's `new Date().toISOString()` (used on the
frontend to timestamp every change) produces a string like
`2026-07-22T19:10:46.276Z`. The trailing `Z` means "this timestamp is in
UTC" — Python's datetime parser reads this as a **timezone-aware** datetime
(it carries an explicit timezone marker). However, when FastAPI/SQLAlchemy
reads a timestamp back out of SQLite, it comes back as a **timezone-naive**
datetime (no timezone marker attached at all, since the `DateTime` column in
the model wasn't configured to store timezone info). Python's `>` comparison
operator refuses to compare an aware datetime with a naive one, since it's
ambiguous which timezone the naive one is actually in — so it raises a
`TypeError` instead of guessing.

**File affected:** `backend/app/services/conflict_resolver.py`

**How it was fixed:** A small helper function, `_normalize_to_naive_utc()`,
was added at the top of the file. Before any comparison or database write
happens, every incoming timestamp is passed through this function: if it has
timezone info attached, it's first converted to UTC (in case it was in some
other timezone) and then has the timezone marker stripped off, leaving a
plain naive datetime that's guaranteed to represent UTC. Both `created_at`
and `updated_at` on every incoming record are normalized this way as the very
first step inside `resolve_and_apply()`, before anything else happens to
them. This guarantees that every datetime being compared or stored from this
point onward is consistently naive UTC, so the `>` comparison always works
safely. This was verified by re-running the exact same sync request that had
previously failed, using the exact `Z`-suffixed timestamp format the browser
actually sends, and confirming both a fresh sync and a conflicting
(older-timestamp) sync both resolved correctly with no error.

### Error #4 (minor) — Auto-sync only triggered on component remount

**What happened:** This wasn't a crash or error message, but an observed gap
in behavior: while online, newly added or updated deliveries stayed marked
"Saved locally" and did not sync automatically — they only became "Synced"
if the "Sync Now" button was clicked, or if the Dispatcher/Agent view was
switched and switched back, or if the page was refreshed.

**Why it happened:** The auto-sync setup (`startAutoSync`) was only wired to
run in two situations: once when the `AgentDeliveryList` component first
mounted, and again whenever the browser's `online` event fired (i.e. going
from offline to online). Switching views or refreshing the page happened to
also re-trigger a sync, but only as a side effect of the component
re-mounting from scratch — not because of any deliberate "sync periodically"
logic. If the user stayed on the Agent view continuously while already
online, nothing would prompt another sync attempt until one of those
specific events happened again.

**File affected:** `frontend/src/services/syncEngine.js`

**How it was fixed:** A third auto-sync trigger was added: a
`setInterval()` that calls the sync check every 15 seconds while the browser
is online, independent of component mounting or connectivity-change events.
The cleanup function returned by `startAutoSync()` was also updated to clear
this interval (in addition to removing the `online` event listener), to
avoid leaving a timer running in the background if the component using it
is ever removed from the page.

---

## Phase 4 — Dispatcher Dashboard Polish & Agent View Improvements

**Goal:** Add status filtering to the Dispatcher dashboard, and address two
usability gaps noticed in the Agent view during testing: deliveries appeared
in arbitrary order, and there was no way to remove a delivery record.

**What was built:**
- `backend/app/routes/deliveries.py` — added a `DELETE /deliveries/{id}`
  endpoint. Deliberately designed to not error if the record doesn't exist
  on the server (returns `{"deleted": false, "reason": "not found on
  server"}` instead) — this matters because a record might only ever have
  existed locally and never been synced, which is a normal, expected case,
  not an error condition.
- `frontend/src/services/indexedDb.js` — added `deleteDeliveryLocally(id)`
  to remove a record from local IndexedDB storage.
- `frontend/src/services/api.js` — added `deleteDeliveryOnServer(id)` to
  call the new backend endpoint.
- `frontend/src/components/AgentDeliveryList.jsx` — two changes:
  1. `loadFromLocalStorage()` now sorts records by `updated_at` descending
     (most recently changed first), instead of relying on IndexedDB's
     arbitrary/insertion order.
  2. Added a `handleDelete()` function and a Delete button per card, with a
     confirmation prompt. Deletes locally first always; if the record was
     already synced, also attempts to delete it from the server (a
     best-effort call — if this fails, e.g. because the agent is offline,
     the error is only logged to the console, since the local delete
     already succeeded and is what matters for the agent's immediate view).
- `frontend/src/components/DispatcherTable.jsx` — added a status filter
  dropdown (All / Picked Up / Out for Delivery / Delivered / Failed
  Attempt) with a "Showing X of Y" count, and sorted the table by most
  recently updated first, matching the Agent view's sort order.

**Errors / issues faced in this phase:** None — each piece was verified
before being shared: the DELETE endpoint was tested with `TestClient`,
including the edge case of deleting a record that was already deleted (to
confirm it returns a clean "not found" response instead of crashing), and
all modified JS/JSX files were syntax-checked before delivery.

**A process note worth recording:** these two additions (ordering and
delete) were not part of the original Phase 4 scope in the ticket list —
they came up organically while testing Phase 3's sync behavior. This is a
completely normal and expected part of real software development: using a
feature surfaces small gaps that weren't obvious during planning. Deciding
where to slot them in (here: added into Phase 4 rather than deferred) is
itself a small scoping decision worth being able to explain.

## Phase 5 — Final Polish & Resume Readiness (not started)

*(This section will be filled in once Phase 5 work happens.)*

---

## Why This Log Matters

Every one of the four issues above is a genuine, realistic bug — not
something contrived for practice. Being able to explain any of them in an
interview (what happened, why, and how it was diagnosed and fixed) is a much
stronger signal of real understanding than simply saying "the project
works." Recruiters and interviewers responding to a portfolio project often
ask "what was the hardest bug you ran into building this?" — this document
is meant to make that question easy to answer in detail, using your own
words, at any point in the future.
