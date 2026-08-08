# Feature Log — What, Why, and What It Does

This file tracks every feature built into this project, in the order it
was added: what was missing before it existed, why it was needed, and
what it actually does now. This is different from `PROJECT_WORKFLOW.md`
(which logs bugs and how they were fixed) — this file is about *decisions*
and *reasoning*, so you can explain not just how something works, but why
it exists at all.

This file is maintained going forward — every new feature gets an entry
here the same day it's built, not added retroactively.

---

## Phase 1 — Backend Foundation

**What was missing:** No backend existed at all — no way to store or
retrieve delivery data anywhere.

**Why it was needed:** Every other feature in this project depends on
having somewhere to persistently store delivery records and expose them
over HTTP. This is the foundation everything else builds on.

**What it does:** A FastAPI backend with a `DeliveryRecord` model and
CRUD endpoints (create, read, update, list), backed by SQLite.

---

## Phase 2 — Offline-Capable Frontend

**What was missing:** No frontend, and more specifically, no way for the
app to function without an internet connection.

**Why it was needed:** This is the actual core premise of the entire
project — delivery agents lose connectivity constantly (basements,
elevators, rural areas), and most delivery apps just fail or block in
that situation. Without offline capability, this project would just be
another CRUD app with no real differentiator.

**What it does:** A React frontend using IndexedDB as local-first storage.
Every change an agent makes saves to IndexedDB immediately, regardless of
connectivity.

---

## Phase 3 — Sync Engine with Conflict Resolution

**What was missing:** Offline changes had nowhere to go — they'd sit in
IndexedDB forever with no way to reach the server, and no plan for what
happens if the same delivery was changed in two places while offline.

**Why it was needed:** Offline storage alone is only half the problem —
a real system needs to reconcile offline changes with the server once
connectivity returns, and handle the case where two different changes
conflict.

**What it does:** A `/sync` endpoint and client-side sync engine that
pushes pending offline changes to the server, using a last-write-wins
strategy (based on timestamps) to resolve conflicts automatically.

---

## Phase 4 — Dispatcher Filtering & Agent List Fixes

**What was missing:** No way to filter the dispatcher's delivery list by
status, and delivery ordering was arbitrary (whatever order IndexedDB
happened to return).

**Why it was needed:** These came up organically while testing Phase 3 —
using the app surfaced real usability gaps that weren't obvious during
planning: a dispatcher managing more than a handful of deliveries needs
filtering, and an agent needs predictable ordering, plus a way to remove
records.

**What it does:** Status filtering + a "Showing X of Y" count on the
dispatcher table; sorting and delete functionality on the agent view.

---

## Authentication — Login, Signup, Logout, Roles

**What was missing:** Every agent was hardcoded as `"agent-001"` — there
were no real user accounts, no way to distinguish one agent from another,
and no concept of a dispatcher role at all.

**Why it was needed:** A hardcoded placeholder agent isn't a real,
deployable product — it was a stand-in to get the sync logic working
first. For the app to have real users, it needs real accounts, and for
dispatchers and agents to have different permissions/views, it needs
role-based access.

**What it does:** JWT-based signup/login/logout, with two roles (`agent`,
`dispatcher`) that gate what each user can see and do.

---

## Dispatcher-Assigned Deliveries (replacing self-created "sample" deliveries)

**What was missing:** Agents could only create their own placeholder
"sample" deliveries — there was no real dispatcher-to-agent assignment
workflow, which doesn't reflect how real delivery operations work.

**Why it was needed:** In a real delivery company, a dispatcher assigns
work to agents — agents don't invent their own deliveries. This also
directly enables role-based permissions to mean something (dispatcher-only
creation, agent-only fulfillment).

**What it does:** Dispatchers assign deliveries to a specific agent from
a dropdown; agents automatically pull in (sync down) whatever's been
assigned to them.

---

## Search Bar (Agent + Dispatcher)

**What was missing:** No way to find a specific delivery without manually
scrolling through the full list.

**Why it was needed:** As delivery volume grows, scanning a full list
becomes impractical — this is standard functionality any real operations
tool needs.

**What it does:** Search by order ID (agent view) or order ID/agent name
(dispatcher view), filtering the visible list live as you type.

---

## Sort + Advanced Filter (status, date range, agent) & Delivery Detail Modal

**What was missing:** Filtering was status-only, with no way to narrow by
agent or date, and no way to see a delivery's full details beyond what
fit in a table row.

**Why it was needed:** A dispatcher managing multiple agents over time
needs to answer questions like "what did this agent do last Tuesday?" —
which requires combining filters, not just one at a time. A table row
also can't show everything about a delivery (full notes, exact
timestamps) without becoming unreadable.

**What it does:** Combinable status/agent/date-range filters, a sort
dropdown, and a click-through modal showing a delivery's full details.

---

## Status History / Audit Log

**What was missing:** The detail modal showed a delivery's *current*
state, but nothing about how it got there — no record of who changed
what, or when.

**Why it was needed:** Accountability and traceability are core to any
real operations tool — if a delivery was marked "failed" incorrectly, or
a dispatcher needs to know who last touched a record, there needs to be
an actual audit trail, not just a snapshot of the current state.

**What it does:** Every create/update action writes a history entry
(who, what changed, when), shown as a timeline in the detail modal.

---

## Toast Notifications

**What was missing:** Sync results, assignment confirmations, and errors
were shown as plain inline text sitting in the page — easy to miss, and
not a pattern real users expect from a polished app.

**Why it was needed:** Plain inline text messages are a placeholder
pattern, not a real UI pattern — most production apps use transient,
visually distinct notifications so the user notices feedback without it
disrupting the layout.

**What it does:** Auto-dismissing notification cards (bottom-right) for
sync results, delivery assignment, and delete confirmations.

---

## Summary Stat Cards (Dispatcher Dashboard)

**What was missing:** A dispatcher had to scroll/filter the full table
just to answer basic questions like "how many deliveries failed today?"

**Why it was needed:** Dashboards exist to give an at-a-glance operational
picture — forcing a dispatcher to manually count rows defeats the purpose
of having a dashboard at all.

**What it does:** Live counts for each status, plus "Delivered Today," at
the top of the dispatcher view.

---

## Agent Ordering Fix (stable list order)

**What was missing:** Sorting the agent's list by "most recently updated"
caused a delivery to visually jump to the top the moment its status was
changed.

**Why it was needed:** This was a direct usability complaint — an agent
working through a list shouldn't have items reordering under them mid-task,
since that's disorienting and error-prone in a real field-work context.

**What it does:** The agent's list now sorts by creation order, which
stays stable regardless of what status changes happen afterward.

---

## Agent Performance View

**What was missing:** No way for an agent to see their own work summary —
completed today, completed this week, current workload.

**Why it was needed:** A real delivery agent (and their employer) cares
about individual performance tracking, not just the raw list of
deliveries — this is standard in any operations/logistics tool.

**What it does:** A dedicated sidebar view showing completed
today/this-week counts, in-progress count, failed attempts, and a
completion rate.

---

## Pagination

**What was missing:** Both the agent and dispatcher views rendered every
matching delivery at once, with no limit.

**Why it was needed:** This doesn't scale — a dispatcher managing
hundreds of deliveries, or an agent with a long work history, would face
a slow, unwieldy, endlessly-scrolling page.

**What it does:** Both views now page results (5 per page for agents, 8
per page for the dispatcher table) with Previous/Next controls.

---

## Full Visual Redesign ("Fleet Ops Console" theme)

**What was missing:** The app used default, unstyled browser form
elements throughout — functional, but looking like an early-stage student
project rather than a real product.

**Why it was needed:** You were explicit that this needed to look and
feel like something worth actually deploying and using — not just a
backend with a bare-minimum UI on top. Visual polish is also part of what
makes a portfolio project stand out.

**What it does:** A cohesive dark theme (custom color palette, Space
Grotesk/Inter/JetBrains Mono typography, sidebar navigation, styled
tables/cards/badges/modals) applied consistently across every screen.

---

## Light / Dark Theme Toggle

**What was missing:** The redesign only shipped a dark theme — there was
no way to switch to a light theme, and no guarantee that a light theme
would even be readable if one existed (dark-tuned colors often fail
contrast on a white background).

**Why it was needed:** You explicitly asked for both themes with a
toggle, and specifically flagged that every button and piece of text must
stay clearly visible after switching — not just an inverted color scheme
that happens to look broken in one mode.

**What it does:** A toggle button (sidebar footer, and on the Login/Signup
pages) switches between themes instantly, persisted in localStorage so it's
remembered on return visits. Every component uses the same CSS variable
names in both themes — only the underlying color values change (defined
once in `theme.css`), so no component contains theme-specific logic. Status
and semantic colors are deliberately re-tuned (not just inverted) for the
light theme, since colors bright enough to read on near-black fail contrast
on white.

---

---

## Delivery Time Estimates ("Expected By")

**What was missing:** No way to communicate or track a deadline for a
delivery — a dispatcher couldn't flag "this needs to arrive by 6pm," and
nothing surfaced when a delivery was running late.

**Why it was needed:** Real delivery operations run on time windows —
this is standard operational depth expected of a genuine logistics tool,
not just a status tracker.

**What it does:** An optional `expected_by` datetime, set when a
dispatcher assigns a delivery (single or bulk). Shown on the agent's card,
the dispatcher's table, and the detail modal — automatically flagged in
red as "(Overdue)" if the deadline has passed and the delivery isn't yet
marked delivered.

---

## Route Optimization / Batching

**What was missing:** An agent's deliveries had no concept of geography —
just a flat list in assignment order, regardless of where anything
actually was.

**Why it was needed:** A real delivery agent working multiple stops
benefits enormously from knowing which deliveries are near each other and
a sensible order to visit them in — this is a core "operational depth"
feature of any real logistics tool, not just a nice-to-have.

**What it does:** An optional `zone` (free-text area name) and optional
latitude/longitude on each delivery. The agent's "Suggested Route" panel
groups active deliveries by zone, and — where coordinates are available —
orders them within each zone using a nearest-neighbor heuristic (starting
from the agent's live location via browser geolocation, with a disclosed
fallback if permission is denied). No paid maps/geocoding API was used or
needed. Deliveries without coordinates still work fine — they're just
grouped by zone rather than precisely ordered, which is disclosed in the
UI rather than hidden.

---

## Bulk CSV Import

**What was missing:** A dispatcher could only assign one delivery at a
time through the form — there was no way to onboard many orders at once.

**Why it was needed:** Real dispatch operations regularly receive orders
in bulk (e.g. an end-of-day CSV export from an order-management system) —
one-by-one assignment doesn't scale to real volume.

**What it does:** A dispatcher uploads a CSV (`order_id`,
`agent_username` required; `notes`, `zone`, `expected_by` optional). Each
row is validated and processed independently — a bad row (unknown agent,
blank order ID) is reported with a clear per-row error, while every valid
row in the same file still succeeds. Built with a hand-written CSV parser
(not a naive comma-split) so notes containing commas, quoted fields, and
Windows-style line endings all parse correctly — tested directly against
those specific edge cases before being wired into the UI.

---

## Multi-Tenant Support

**What was missing:** Every user and delivery lived in one shared, global
space — there was no concept of "which company does this belong to."
Deploy this for two different delivery businesses and they'd see each
other's agents, deliveries, and dashboards.

**Why it was needed:** A real deployment needs to serve more than one
organization on the same running app without their data ever mixing —
this is what makes the project a genuine multi-customer product rather
than a single-team internal tool.

**What it does:** Every user belongs to exactly one organization,
established at signup: either **create** a new one (entering an org name
— you become its admin automatically) or **join** an existing one via an
8-character invite code shown once at creation (and re-visible to the
admin afterward — see Admin Panel below). Every single query in the app —
listing deliveries, listing agents, fetching history, exporting CSVs —
filters by the caller's `org_id`, verified with two separate test
organizations and confirmed each saw zero of the other's data.

**A real vulnerability found and fixed while building this:** the offline
`/sync` endpoint is intentionally unauthenticated (see Rate Limiting
below for why), which meant a crafted payload could reference an
existing delivery ID belonging to a *different* organization and
overwrite it, as long as it paired that ID with one of its own agent
IDs. Fixed by verifying the existing record's organization matches the
requesting agent's organization before allowing any update — confirmed
by actually simulating this exact attack in testing and watching it get
correctly rejected.

---

## Admin Panel

**What was missing:** No way to see who belonged to an organization, no
way to disable a departing employee's access, and no way to help someone
who forgot their password — accounts, once created, were permanently
fixed with no management capability at all.

**Why it was needed:** Any real organization using this needs basic user
administration — this is baseline expected functionality for a
multi-user product, not an optional extra.

**What it does:** A dedicated `admin` role (the first person to create an
organization becomes its admin automatically) can view every user in
their organization, deactivate or reactivate an agent's account
(deactivation takes effect immediately — even blocking that user's
*already-issued* login token, not just future login attempts), and reset
a user's password directly. Honest, disclosed limitation: since there's
no email service, "reset password" means the admin sets a new one and
shares it with the person themselves — not an emailed reset link, which
is what a production system would use instead.

**A gap fixed before calling this done:** admins initially had no
sidebar link to the actual delivery dashboard at all — only "Manage
Users" — meaning the very first user of any brand-new organization would
have had no way to assign a single delivery, despite the backend already
permitting it. Fixed by giving admins both links, defaulting to the
Dashboard as their landing view. Separately, the signup screen promised
"any admin can look up the invite code later," but no such lookup
actually existed anywhere — built the missing endpoint and admin-panel
display specifically so that promise is true.

---

## Analytics / Reporting Export

**What was missing:** Delivery data only ever lived inside the app —
there was no way to get it out for use in a spreadsheet or an external
reporting tool.

**Why it was needed:** Real operations teams need to analyze delivery
data outside the app itself (monthly reports, sharing with stakeholders
who don't have an account, etc.) — this is standard expected
functionality for any operational dashboard.

**What it does:** A dispatcher/admin can download a CSV of their
organization's deliveries, optionally filtered to a date range using the
same From/To fields already used for table filtering. Built with
Python's built-in `csv` module (not hand-built comma-joined strings), so
a notes field containing a comma still exports correctly — deliberately
avoiding the exact category of bug the bulk-import CSV *parser* was
built to prevent on the way in, this time on the way out.

---

## Rate Limiting & Security Hardening

**What was missing:** No protection against automated abuse — the same
signup or login endpoint could be hit as fast as a script could send
requests, the JWT signing key was hardcoded directly in the source code,
and CORS was wide open to any origin.

**Why it was needed:** This was explicitly flagged as a known gap in
`docs/SECURITY_AND_ACCESS.md` from early in the project — necessary
before this could honestly be called ready for any real deployment.

**What it does:**
- Signup and login are rate-limited per IP (5/min and 10/min
  respectively) using `slowapi`, confirmed by actually sending 7 rapid
  signup requests and watching the 6th and 7th get correctly rejected
  with a 429
- The JWT signing key now reads from a `JWT_SECRET_KEY` environment
  variable, falling back to a dev-only value with a loud startup warning
  if it's not set — rather than a fixed value baked into the source code
- The `/sync` endpoint is deliberately left without login-based
  authentication (an offline device may not have a fresh valid session)
  but is rate-limited instead, and organization membership is still
  enforced server-side per record (see the Multi-Tenant fix above) rather
  than trusted from the client
- CORS origins are now configurable via an `ALLOWED_ORIGINS` environment
  variable instead of being permanently wide-open
- A few standard, low-risk security headers (`X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`) are added to every response

**Honestly disclosed, not fixed:** a shared-store rate limiter (e.g.
Redis-backed) would be needed for a real multi-server deployment, since
the current in-memory limiter only tracks requests per individual server
process — documented as a known gap rather than silently left unmentioned.

---

## Real Customer Portal (Accounts, Dashboard, In-App Notifications)

**What was missing:** The only customer-facing experience was a public
tracking link and console-logged email/SMS text — accurate for local
development, but not something a real user (or a recruiter checking a
deployed link) would ever actually see or use as a product feature.

**Why it was needed:** A genuine delivery platform needs a real customer
account system, not a simulation of one — this was flagged directly as
the difference between "a mini project" and a complete product.

**What it does:** A completely separate customer identity system
(`CustomerDB`, its own signup/login, its own JWT token shape so it can
never be confused with a staff token) — customers aren't tied to any one
organization, since they may order from several different companies on
this platform. Deliveries auto-link to a matching customer account by
email, both at creation time and retroactively if the customer signs up
afterward. The dashboard shows every linked order across all
organizations, with an expandable per-order timeline, proof of delivery,
and a rating form. A real in-app notification bell (unread count,
mark-read, polling) is now the **primary** notification channel — the
console-log email/SMS from the previous entry is kept only as a
secondary, external channel for reaching someone not currently logged in.

**A real routing bug found and fixed after initial delivery:** the
customer portal was unreachable in a browser that already had a staff
session saved, because the top-level router checked "is a staff user
already logged in?" before ever offering the staff/customer choice — so
an existing staff session silently took priority every time, with no way
to reach the customer portal at all. Fixed by adding a `?portal=customer`
/ `?portal=staff` URL override that always wins regardless of any saved
session, plus visible links to it from both the staff sidebar and the
staff login screen — not just relying on a first-visit chooser that a
returning user would never see again.

---

## (Template for future entries — copy this structure)

## Feature Name

**What was missing:**

**Why it was needed:**

**What it does:**
