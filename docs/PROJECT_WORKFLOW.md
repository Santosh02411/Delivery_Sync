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

## Phase 5 — Authentication, Roles & Delivery Assignment

**Goal:** Replace the placeholder single-agent setup (a hardcoded
`agent-001`) with real accounts, login/logout, and role-based access —
agents and dispatchers as genuinely different users with different
permissions, plus a real assignment flow (dispatcher assigns work to a
specific agent) instead of agents self-creating "sample" deliveries.

**What was built:**
- `backend/app/models/user.py` — `UserDB` table (username, hashed
  password, role, display name) plus signup/login/output schemas
- `backend/app/services/auth.py` — password hashing (bcrypt via passlib)
  and JWT creation/verification
- `backend/app/routes/auth.py` — `/auth/signup`, `/auth/login`, and a
  `get_current_user` dependency other routes use to identify who's calling
- `backend/app/routes/deliveries.py` — updated so `POST /deliveries/`
  (create/assign) is dispatcher-only, `GET /deliveries/` (full list) is
  dispatcher-only, and a new `GET /deliveries/mine` returns only the
  logged-in agent's assigned deliveries
- `backend/app/routes/users.py` — `GET /users/agents`, letting a
  dispatcher list all registered agents for the assignment dropdown
- Frontend: `AuthContext.jsx` (session state, persisted to localStorage),
  `LoginPage.jsx` / `SignupPage.jsx`, and a "pull-sync" addition to the
  Agent view — agents now periodically fetch their assigned deliveries
  from the server (`fetchMyDeliveriesFromServer`) and merge them into
  IndexedDB, rather than only ever seeing locally self-created records

**Errors faced in this phase:**

1. **`passlib`/`bcrypt` version incompatibility.** Newer `bcrypt` releases
   removed an internal attribute (`__about__.__version__`) that `passlib`
   expects, causing password hashing to fail immediately on signup. Fixed
   by pinning `bcrypt==4.0.1` in `requirements.txt` — a real, common
   dependency-compatibility issue, not a mistake in the app's own code.

2. **File-sharing naming collisions.** Several new files were both named
   `auth.py` (one under `models/`, one under `services/`, one under
   `routes/`) — Claude's file-sharing tool can't present multiple files
   with the same display name at once, so they were shared under
   temporary names (`auth_service.py`, `auth_routes.py`). This caused real
   confusion: pasting a file under its *shared* name instead of renaming
   it back to what the code actually imports (`auth.py` in each folder)
   produced `ImportError` / `ModuleNotFoundError` on backend startup.
   **Lesson:** when a shared filename differs from its destination
   filename, that rename step is not optional — Python resolves imports by
   the literal file name on disk, not by any label attached when it was
   shared.

3. **CORS misconfiguration (`allow_origins=["*"]` + `allow_credentials=True`).**
   Once the frontend started sending real requests to protected endpoints,
   syncing failed with a generic "Failed to fetch." Root cause: browsers
   reject a CORS response that allows any origin AND allows credentials at
   the same time (a deliberate security restriction). Fixed by setting
   `allow_credentials=False`, since this project doesn't use cookies.

---

## Phase 6 — Status History, Toast Notifications & Summary Stats

**Goal:** Add an audit trail (who changed what, when), replace plain
inline status messages with proper toast notifications, and give the
dispatcher a live summary of delivery counts by status.

**What was built:**
- `backend/app/models/delivery_history.py` + `services/history.py` — a
  `delivery_history` table logging every create/update (old status → new
  status, who did it, when), written to automatically from both the
  normal `PATCH /deliveries/{id}` route and the offline `/sync` path
- `GET /deliveries/{id}/history` — returns the full timeline for one delivery
- Frontend: `DeliveryDetailModal.jsx` fetches and displays this timeline;
  `ToastContext.jsx` provides app-wide toast notifications, replacing
  inline "Synced!" / "Assigned!" text; `DispatcherTable.jsx` gained stat
  cards (counts per status, delivered-today) computed from the same data
  already being fetched — no extra API calls needed

**Errors faced in this phase:** None new — this phase built cleanly on
the now-stable auth/assignment foundation from Phase 5, and each piece
(history logging, the `/history` endpoint) was verified with
`TestClient` before being wired into the frontend.

---

## Phase 7 — Full Visual Redesign & Light/Dark Theme

**Goal:** Replace the plain, inline-styled interface with a genuine
visual identity — sidebar navigation, a proper typography system, and a
dark "fleet ops console" aesthetic fitting a logistics product, plus a
light/dark toggle with guaranteed readability in both modes.

**What was built:**
- `frontend/src/styles/theme.css` — one shared set of CSS variables
  (colors, fonts, spacing) used by every component; a
  `html[data-theme="light"]` override block redefines those same variable
  names with re-tuned (not just inverted) values for light mode
- `ThemeContext.jsx` — toggles the `data-theme` attribute and persists the
  choice to localStorage
- `Sidebar.jsx` — real navigation (My Deliveries / Performance for agents,
  Dashboard for dispatchers), replacing the old top-bar view switcher
- Every existing component (`AgentDeliveryList`, `DispatcherTable`,
  `DeliveryDetailModal`, badges, buttons, auth pages) was rewritten to use
  the shared theme classes instead of one-off inline styles
- New: `AgentPerformance.jsx` (completed today/this week, completion
  rate) and `Pagination.jsx` (used by both the Agent and Dispatcher lists)

**Errors faced in this phase:**

1. **Two small class/style mismatches caught before delivery.** A
   `.mono` utility class was used in JSX but never defined in
   `theme.css`, and one button used a class name
   (`btn-outline-accent`) that didn't match what the stylesheet actually
   defined (`btn-info-outline`). Both were caught by writing a small
   cross-check (comparing every `className` used in the JSX against every
   class actually defined in the CSS) rather than by visual inspection —
   worth knowing as a general technique: for a redesign this size, a
   scripted consistency check catches mismatches that are easy to miss by
   eye, especially for classes that don't render as visibly "broken" (an
   unstyled button still looks like *a* button, just not the right one).

---

## Phase 8 — Operational Depth (Route Batching, Time Estimates, Bulk Import)

**Goal:** Add features expected of a genuine logistics tool beyond basic
status tracking: grouping/ordering an agent's deliveries by area, a
deadline concept with overdue flagging, and bulk-assigning many
deliveries at once from a CSV instead of one at a time.

**What was built:**
- `backend/app/models/delivery.py` — added `zone`, `latitude`,
  `longitude` (all optional strings) and `expected_by` (optional
  datetime) to the delivery record
- `backend/app/routes/bulk_import.py` — `POST /deliveries/bulk-import`,
  processing each CSV row independently so one bad row (unknown agent,
  blank order ID) doesn't block the valid rows in the same file
- `frontend/src/services/csvParser.js` — a hand-written CSV parser
  (quoted fields, embedded commas, escaped quotes, CRLF line endings all
  handled) rather than a naive `text.split(',')`, which breaks the moment
  a notes field contains a comma
- `frontend/src/services/routeOptimizer.js` — groups an agent's active
  deliveries by zone, and — where coordinates are available — orders them
  within each zone via a nearest-neighbor heuristic (a deliberate,
  explainable approximation, since true optimal routing is NP-hard),
  anchored to the agent's live position via browser geolocation where
  permitted
- `SuggestedRoute.jsx`, `BulkImportPanel.jsx` — the corresponding UI

**Errors faced in this phase:**

1. **Stale SQLite schema after adding new columns.** After adding `zone`,
   `latitude`, `longitude`, and `expected_by` to the `DeliveryRecordDB`
   model, syncing and fetching deliveries started failing with
   `sqlalchemy.exc.OperationalError: no such column: deliveries.zone`.
   Root cause: `Base.metadata.create_all()` (called on every backend
   startup) only creates tables that don't exist yet — it does **not**
   alter an already-existing table to add newly-defined columns. Since
   `database.db` had been created earlier in the project (back when the
   table only had the original columns), it kept its old structure no
   matter how many times the backend restarted, until the file itself was
   deleted so a fresh one could be created with the current, correct
   schema. **This is a genuine, common real-world issue** — production
   systems handle this with a proper migration tool (e.g. Alembic) that
   applies incremental schema changes to an existing database without
   losing its data; deleting and recreating the file is only an
   acceptable fix here because this is local development/demo data with
   nothing worth preserving. Worth stating plainly in an interview if
   asked "how would you handle a schema change in production?" — the
   honest answer is "not the way this project did it locally."

---

## Phase 9 — Final Polish & Resume Readiness (not started)

*(This section will be filled in once Phase 9 work happens.)*

---

## Phase 10 — Recurring Orders & Marketplace Polish

**Recurring/subscription orders**, built on top of a design decision
made explicitly up front (avoids the two easy wrong turns a "make it
recurring" feature invites): payment is never auto-charged — every
cycle is a real `pending_payment` Order the customer must confirm and
pay themselves, same as an abandoned-cart order today. This meant the
actual engineering work was almost entirely reuse rather than new
payment logic: `routes/subscriptions.py`'s `initiate-payment` endpoint
is a near-line-for-line copy of `checkout()`'s Razorpay/COD/test-mode
tail, applied to an order the scheduler already built instead of one
built fresh from a cart — and `POST /customer/checkout/verify` needed
zero changes at all, since it was already written generically against
"any pending_payment order owned by this customer," not "an order this
endpoint just created." The one real new piece is
`services/subscription_scheduler.py`'s `run_subscription_cycle` — an
`asyncio` background task (not APScheduler/Celery — this project stays
zero-extra-infra, and a 60-second `asyncio.sleep` loop started from
FastAPI's `on_event("startup")` is genuinely sufficient at this scale)
that finds every due subscription, builds its order at *today's* stock
and prices (never the prices from when the subscription was created —
a subscription is "reorder this," not "re-charge this exact receipt"),
and always advances `next_run_date` by the interval regardless of
whether that cycle's order ever gets paid, so an ignored reminder can't
silently freeze every future cycle too.

**Multi-vendor marketplace**: investigating this ticket turned up that
the hard part — real multi-tenancy — already existed. `OrganizationDB`
was already "one org = one independently-run store"; `CustomerDB` was
already a global identity deliberately separate from the org-scoped
staff `UserDB` specifically because a customer "may have deliveries
from many different companies" (see that model's own docstring);
`CartItemDB` was already scoped to one store at a time "same behavior
as Swiggy/Zomato/Amazon-marketplace carts"; and `GET /stores` already
listed every opted-in store to browse. What was missing was purely
findability: a store had nothing beyond a bare name to distinguish it
in a directory of many, and no way to search or filter that directory
at all. Fixed with two new optional `OrganizationDB` columns
(`category`, `description`) and query params on the existing `GET
/stores` endpoint — no new tables, no change to the cart/checkout
architecture, because none was needed.

Both features passed full TestClient verification (subscription
create → run-now → initiate-payment → verify → paid-with-real-delivery;
insufficient-stock line-skipping; cross-customer subscription-ownership
isolation; invalid-interval rejection; marketplace search/category
filtering, case-insensitive) and a full-project `esbuild` bundle check
came back clean.

---

## Adding Admin Action Logging Without Breaking Offline Caching

Two features requested together: a general admin action log ("who
changed what, when" beyond just delivery status), and pagination on
the large list endpoints. The action log was straightforward — a new
table, a small diffing helper, and a handful of `record_action(...)`
calls dropped into existing write endpoints. The pagination part had
a real trap in it.

The naive version — add `limit`/`offset` with sane defaults to every
unpaginated list endpoint — would have been wrong for two of them.
`GET /deliveries/` and `GET /deliveries/mine` (the dispatcher/agent
lists) don't just render a table; their response is also what gets
written into IndexedDB via `cacheDispatcherDeliveries()` /
`cacheCustomerDeliveries()`-equivalent calls, which is the entire
mechanism that makes the dashboards usable when a delivery agent loses
signal in the field — the whole premise of this being an "offline-
first" app. Slapping a default `limit=100` on that endpoint (which I
did briefly, then caught before shipping it) would have silently
truncated the offline cache for any org with more than 100 delivery
records — the kind of bug that wouldn't show up in a demo with a
handful of seeded deliveries, only months later when it actually
matters, and offline mode would just quietly stop working for older
records with no error anywhere.

The fix: leave those two endpoints as full, unpaginated fetches (with
a comment explaining why), since the dispatcher table already paginates
on-screen client-side over the fully-cached data — the right layer to
page something that's already local. Applied real server-side
pagination only where there's no offline-cache dependency:
`/customer/orders` and `/customer/notifications` (default `limit=20`),
plus `/customer/deliveries` with limit/offset made *optional* rather
than defaulted, since that one endpoint does double duty — it's both
the main "load my orders" call AND the seed for the customer's own
offline delivery cache.

That optional-pagination fix surfaced one more real bug in a code
review pass: `CustomerDeliveryCard`'s cancelled-order handling called
`fetchMyOrders(token)` with no filter, then searched the returned array
client-side for the one order matching the current delivery, purely to
read its refund status. Once `/customer/orders` got a `limit=20`
default, that lookup would silently return nothing for any cancelled
order more than 20 purchases back — refund status would just stop
showing up for older cancellations, no error, easy to miss entirely in
testing since it only breaks past the 20th order. Fixed by adding a
`delivery_id` query filter to `GET /customer/orders` and switching that
one call site to use it, instead of scanning an increasingly-partial
list.

Lesson worth remembering: "add pagination to the unpaginated endpoints"
sounds like a mechanical, uniform change, but a couple of these
endpoints were quietly load-bearing for something other than what a
list endpoint normally does (seeding an offline cache; being scanned
client-side as an ad-hoc lookup). Grepping for every call site of an
endpoint before changing its default behavior — not just skimming the
route handler itself — is what caught both issues here before they
shipped.

---

## Reusing an Existing Component Instead of Building a Second One

When adding a live agent-location map to the public tracking page, the
tempting shortcut was to write a new, simpler map component scoped to
"public tracking only." But `LiveTrackingMap.jsx` already existed — it
was built earlier for the logged-in customer dashboard, complete with
offline tile caching, a WebSocket live-update subscription, and a
30-second polling safety net. Writing a second component would have
meant maintaining two copies of all of that, one of which would
inevitably drift out of sync with the other over time (a classic
source of "why does the map behave differently on these two pages"
bugs).

Instead, the existing component was extended to accept an optional
`token` prop: pass one and it calls the logged-in customer endpoint,
omit it and it calls a new public endpoint instead — same rendering
logic, same caching, same WebSocket handling either way, since the
tracking WebSocket was already unauthenticated (scoped to an
unguessable delivery UUID, the same security model as the public
tracking page itself). The only genuinely new code was the new public
backend endpoint and a two-line branch inside the existing `poll()`
function.

The backend side of that new endpoint got one deliberate restriction
the logged-in version doesn't have: it only returns a position while
the delivery is `picked_up` or `out_for_delivery`. The logged-in
customer endpoint doesn't bother with that check, because ownership
(the delivery has to belong to the requesting customer) already limits
who can ask. The public endpoint has no login at all — anyone with the
tracking link can call it — so it needed its own limit on *when* a
position is exposed, not just relying on the fact that the agent's
identity is never included in the response. Scoping it to exactly the
two statuses the existing location-broadcast WebSocket code
(`routes/users.py`) already uses for its live pushes was a deliberate
choice too: it means the one-off REST fetch and the real-time updates
can never disagree about whether a position counts as "currently
live," which would otherwise be an easy way to introduce a subtle bug
(map shows a stale pin because the REST call succeeded under a looser
rule than the WebSocket was using).

---

## A Route-Ordering Bug Caught Before It Shipped

Adding `PATCH /deliveries/bulk-status` and `PATCH /deliveries/bulk-assign-agent`
looked like a pure addition — new endpoints, nothing existing should
change. The first draft appended both route functions near the bottom
of `deliveries.py`, after the existing single-record
`PATCH /{delivery_id}`. That's the kind of ordering mistake that's easy
to miss in review, because the code reads fine top to bottom and every
individual endpoint is correct in isolation.

FastAPI (like most routers) matches routes in declaration order, and
`/{delivery_id}` is a wildcard that matches *any* path segment —
including literally the string "bulk-status". With the bulk routes
declared after it, a request to `PATCH /deliveries/bulk-status` would
never reach the bulk handler at all: it'd match `/{delivery_id}` first,
try to look up a delivery with the literal id `"bulk-status"`, and
return a 404. The bulk endpoints would have been completely
unreachable, and nothing in a quick manual check (hitting them
directly, expecting a 404 on a fresh/empty selection anyway) would
have made that obvious — the failure mode looks identical to "no
matching deliveries," not "route doesn't exist."

Caught it by remembering the file already had this exact class of
ordering requirement — `/unassigned` and `/mine` are deliberately
declared before their own `/{delivery_id}` catch-alls, which is
mentioned nowhere in a comment, just baked into the file's existing
route order. Grepping the file's full endpoint list before adding
anything new — not just eyeballing where the new code visually fit —
surfaced the mismatch immediately, and the fix was a pure reordering,
no logic changes needed. Same lesson as the offline-cache pagination
issue from the previous session: a change that looks additive can
still interact with an existing, unstated invariant elsewhere in the
file, and the way to catch that is to check the file's actual current
behavior before assuming "I only added new code" means "nothing else
could have changed."

---

## A Test-Isolation Bug That Was Already There, Waiting

Three new backend test files landed alongside the security-hardening
work. All three passed individually. Run as part of the FULL suite,
four tests failed — two of them in a file (`test_staff_account_settings.py`)
that hadn't been touched this session at all, with an error that made
no sense on its face: "Another account already uses that email," for
an email literal that appeared nowhere else in the entire test suite.

Bisecting which combination of test files triggered it narrowed the
cause to one specific file always being present: `test_rate_limiting.py`.
That file has a legitimate, documented reason to do something unusual —
slowapi's rate limiter reads a `TESTING` environment variable at
*import time* to decide whether it's active at all, and every other
test file wants it OFF (hammering an endpoint in a tight test loop
isn't a real abuse pattern worth tripping over). So this one file
flips `TESTING` on, and to make that take effect, it deletes `main`
and every `app.*` module from Python's `sys.modules` cache and
reimports them fresh — then does the same deletion again on its own
teardown, so the next test in the session gets a normal, un-rate-limited
reimport.

The bug was in what that reimport does to `conftest.py`'s shared
`client` fixture. That fixture overrides FastAPI's `get_db` dependency
so every test talks to its own isolated, temporary SQLite file instead
of the real database — but it was keyed to a `get_db` function
reference imported once, at module collection time, before any test
runs. Once `test_rate_limiting.py` forced a fresh reimport of
`app.db.session`, every route in the newly-reimported `main` was wired
to a *new* `get_db` function object — a different one, in memory,
than the stale reference `conftest.py` was still overriding. FastAPI's
dependency-override dict is keyed by exact object identity, not by
name, so the override silently stopped matching anything. Every test
that ran afterward kept working — no error, no crash — while quietly
writing into the real `backend/database.db` file instead of its own
disposable one.

That's what made it invisible for as long as it was: a leak with no
symptom, right up until a test happened to check for state that a leak
would actually corrupt. Two of this session's new tests did exactly
that — checking that a second signup can't reuse an email already
taken — and got tripped up by a row that had genuinely, accidentally
persisted from an earlier, unrelated test run days before, sitting in
a real file on disk the whole time.

The fix was small once found: re-fetch `get_db` from `app.db.session`
*inside* the `client` fixture, every time it runs, instead of trusting
a reference captured once before any module-reloading trickery could
have made it stale. The real lesson is closer to last session's
route-ordering bug than it might look: something that reads as pure
infrastructure — a shared pytest fixture nobody was actively
editing — can still be quietly wrong in a way that only a new test,
checking something nobody had checked before, will ever surface. "This
file didn't change" is not the same claim as "this file's behavior
didn't change" when anything upstream of it did.

---

## Why This Log Matters

Every issue logged above is a genuine, realistic bug — not something
contrived for practice. Being able to explain any of them in an interview
(what happened, why, and how it was diagnosed and fixed) is a much
stronger signal of real understanding than simply saying "the project
works." Recruiters and interviewers responding to a portfolio project
often ask "what was the hardest bug you ran into building this?" — this
document is meant to make that question easy to answer in detail, using
your own words, at any point in the future.
