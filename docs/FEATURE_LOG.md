# Feature Log — What, Why, and What It Does

This file tracks every feature built into this project, in the order it
was added: what was missing before it existed, why it was needed, and
what it actually does now. This is different from `PROJECT_WORKFLOW.md`
(which logs bugs and how they were fixed) — this file is about _decisions_
and _reasoning_, so you can explain not just how something works, but why
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

**What was missing:** The detail modal showed a delivery's _current_
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
existing delivery ID belonging to a _different_ organization and
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
_already-issued_ login token, not just future login attempts), and reset
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
avoiding the exact category of bug the bulk-import CSV _parser_ was
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

## Real Product Image Upload

**What was missing:** `image_url` on a product was just a free-text field —
a dispatcher had to already have the image hosted somewhere else and paste
in a link. No actual file upload/storage existed.

**Why it was needed:** No real store owner has a pre-hosted image URL
ready to paste in; they have a photo on their phone/laptop. A catalog
management feature isn't real without a way to actually upload the photo.

**What it does:** New `POST /admin/products/upload-image` endpoint takes
real multipart file bytes, validates type (JPEG/PNG/WebP/GIF) and size
(5MB max), and saves it to `backend/uploads/products/<uuid>.<ext>` on
disk — returning a URL that's then mounted and served back out at
`/uploads/products/...` via FastAPI's StaticFiles. `ProductManager.jsx`
got a real file input with upload progress + thumbnail preview;
`Storefront.jsx` now shows product photos in the catalog and cart.
Deleting a product also cleans up its image file from disk.

---

## Real Razorpay Refunds on Cancellation

**What was missing:** Cancelling a paid order only flipped the delivery's
status to "cancelled" in the database — no money ever actually moved back
to the customer, real gateway or not.

**Why it was needed:** A cancel button that doesn't refund isn't a real
cancel button from a customer's (or a recruiter's) perspective — it's a
status label. This was the same "is it actually real, not just a
console-log demo" gap the notification system had earlier.

**What it does:** New `services/refund.py` with `refund_order_for_delivery()`,
called from BOTH places an order can be cancelled — the customer's
self-serve cancel button and the dispatcher/admin status-update path.
It looks up the Order linked to the cancelled delivery and, if it was
actually paid: calls Razorpay's real refund API (same HMAC-verified,
no-shortcuts pattern as the original payment integration) when a gateway
is configured, or marks a clearly-labeled simulated refund when running
in test mode — mirroring `is_test_mode_payment` exactly. New
`refund_status`/`razorpay_refund_id`/`refunded_at` columns on Order.
Refund failures are caught and recorded as `refund_status="failed"`
rather than silently blocking the cancellation itself. Frontend now
shows the real refund outcome ("Refund issued on ..." or a failure
notice) on a cancelled order.

---

## Product Reviews & Ratings

**What was missing:** Only delivery-experience feedback existed (was the
agent good, was it on time) — nothing for rating the product itself.

**Why it was needed:** A store needs product ratings for customers
browsing to decide whether to buy — delivery feedback doesn't tell a
shopper anything about the product quality.

**What it does:** New `ProductReviewDB` table, separate from the existing
delivery-feedback table. Submitting a review is gated server-side, not
just hidden in the UI: the order must belong to the reviewing customer,
must be paid, must actually contain that product, and the linked delivery
must have reached `delivered` — otherwise it's rejected with a clear
error. One review per (order, product) pair, enforced with a DB unique
constraint. New endpoints: public `GET /stores/products/{id}/reviews`
(browse before buying, no login), `GET /customer/deliveries/{id}/reviewable-items`
(what still needs a review on a delivered order), and
`POST /customer/products/{id}/reviews` (submit). Product listings in
both the admin catalog view and the public storefront now show an
aggregated average rating + review count, computed live from real review
rows rather than a cached counter. `CustomerDashboard.jsx` gained a
"Rate your products" section on delivered orders — one star-rating form
per line item, right where the existing delivery-feedback form already
lived.

---

---

## Stock / Inventory Tracking

**What was missing:** Products could be "sold" with no quantity limit —
`stock_quantity` didn't exist at all, so a store could oversell anything
indefinitely.

**Why it was needed:** Real stores run out of things. Without a stock
limit, a shop selling a one-off or limited item has no way to stop
customers from ordering more than actually exists.

**What it does:** New optional `stock_quantity` on Product — `None`
means untracked/unlimited (the old behavior, unchanged for every
existing product), and setting a number turns on real enforcement.
Checked at cart-add, cart-quantity-update, checkout-creation, and once
more right before payment is finalized (the authoritative check, since
stock can change between checkout and payment). Decremented only once
an order actually reaches `paid` — an abandoned checkout never
permanently reserves stock. If stock runs out for a **real** payment
between checkout and verification, the order is auto-refunded via the
existing Razorpay refund integration rather than leaving the customer
charged for nothing. Cancelling a paid order restocks the items
automatically (wired into the same refund path from the previous
session's cancellation work) — the items never shipped, so they go
back on the shelf. `ProductManager.jsx` got a stock field on create
plus an inline editable stock number per product; `Storefront.jsx`
shows "Only N left" / "Sold out" badges and disables Add-to-cart /
blocks quantity increases at the limit.

---

## Coupons / Discounts

**What was missing:** No promo code mechanism anywhere in checkout —
there was no way for a store to run a sale or targeted discount.

**Why it was needed:** Discount codes are a basic, expected e-commerce
feature for both marketing (drive orders with a promo) and support
(comp a customer) — a checkout flow isn't feature-complete without one.

**What it does:** New org-scoped `CouponDB` — percent-off or flat-₹-off,
with optional minimum order value, optional max redemptions, optional
expiry, and an active/inactive toggle. Admin/dispatcher CRUD at
`/admin/coupons`. On the customer side, a coupon can be previewed
against the current cart before committing (`/customer/checkout/validate-coupon`,
used by the cart's "Apply" button for instant feedback) and is then
applied for real at checkout. Eligibility (active, not expired, under
its use limit, minimum order met) is enforced by one shared function
(`services/coupons.py`) used by both the preview and the real checkout,
so what a customer sees in the preview can never disagree with what
they're actually charged. A coupon's `used_count` is only incremented
once its order reaches `paid` — an abandoned checkout never burns
through a limited coupon's redemptions.

---

## Delivery Fee + Tax (GST)

**What was missing:** Checkout charged exactly the product subtotal —
no delivery charge, no tax line, on every single order regardless of
store or location.

**Why it was needed:** No real delivery/e-commerce checkout charges
exactly the product subtotal — a delivery fee and applicable tax (GST,
for an India-focused app) are standard, expected line items, and their
absence made the checkout total simply wrong for demonstrating a real
payment flow.

**What it does:** Each org now has an admin-configurable flat
`delivery_fee` and `tax_rate_percent` (`PATCH /admin/store/pricing`,
new "Delivery Fee & Tax (GST)" card in the Products page). Checkout now
computes, in order: subtotal → minus any coupon discount → GST applied
to that discounted amount → plus the flat delivery fee → the actual
total charged via Razorpay. The full breakdown (subtotal, discount,
delivery fee, tax, total) is returned from the checkout API and stored
on the Order itself, not just computed in the frontend, so a receipt
always reflects exactly what was charged even if the store's pricing
changes later. This also closed a related gap in last session's refund
work: refunds now correctly return the full amount actually charged
(including delivery fee and tax), not just the product subtotal.
`Storefront.jsx`'s cart shows a live subtotal/discount/delivery/GST/total
breakdown before checkout, using the same formula as the backend.

---

---

## Admin Analytics Dashboard

**What was missing:** No revenue/order-volume view anywhere — an admin
could only see raw lists of orders/deliveries and had to mentally
total them up.

**Why it was needed:** "How is the store actually doing" (revenue,
order volume, what's selling, what's running low, what's stuck in
delivery) is one of the first things a real store admin wants to see,
and no amount of scrolling raw lists answers it well.

**What it does:** New admin-only `GET /admin/analytics/` endpoint
(with a `days` window — 7/30/90 in the UI), computed live from
existing Order/OrderItem/DeliveryRecord rows rather than a maintained
running total, so it can never drift from what the raw lists already
show. Returns: total revenue, order count, average order value,
discount/delivery-fee/tax totals, refund totals, a day-by-day revenue
series (zero-filled so a chart has a continuous axis), the top 5
products by revenue, a delivery status breakdown (pending through
delivered/cancelled), and a low-stock alert list (any tracked product
at 5 units or fewer — see last session's stock-tracking work). New
`AnalyticsDashboard.jsx`, added as its own "Analytics" nav item for
admins — stat cards, a lightweight CSS bar chart for the revenue
trend, a status-breakdown bar, a top-products list, and low-stock
warnings. No new charting library — everything is plain divs/CSS to
avoid adding a dependency for what's a fairly simple visual need here.

---

## Push Notifications for Agents/Dispatchers

**What was missing:** Web Push only ever fired for customers (order
status updates). An agent got no notification when assigned a new
delivery, and a dispatcher/admin got no notification when a new
unassigned customer order landed in their queue — both had to keep
the dashboard open and refresh to notice.

**Why it was needed:** The whole point of Web Push (real OS-level
notifications, even with the tab/browser closed) is exactly this kind
of "you need to know the moment this happens" event — and an agent or
dispatcher needs that just as much as a customer does.

**What it does:** Reused the exact same Web Push mechanism already
built for customers (`services/push.py`, VAPID-based, no third-party
account needed) rather than building a second notification pipeline —
`PushSubscriptionDB` now supports a staff subscriber (`user_id`
alongside the existing `customer_id`), with new endpoints
(`/users/me/push/vapid-public-key`, `/users/me/push/subscribe`)
mirroring the customer-facing ones. Two new trigger points in
`services/notifications.py`: `notify_agent_of_new_assignment` (fires
whenever a delivery is assigned to an agent — both at dispatcher-
creation time and via the "assign to agent" action on a customer
order) and `notify_dispatchers_of_new_order` (fires to every
dispatcher AND admin in the org the moment a checkout order lands
unassigned — wired into `verify_payment`). Both are best-effort and
failure-isolated exactly like the customer-facing push, confirmed by
smoke-testing with deliberately invalid subscription endpoints: the
push call fails and logs, but checkout/assignment still succeed.
Frontend: extracted the shared base64→Uint8Array VAPID helper into
`services/pushUtil.js` (previously duplicated per-component) and added
a "🔔 Enable Notifications" button to the staff sidebar, with
role-aware copy explaining what it's for.

---

---

## Delivery Time-Slot Scheduling

**What was missing:** No way for a customer to pick a delivery window —
every order was implicitly "as soon as possible," with no scheduling
concept anywhere in checkout.

**Why it was needed:** Picking a delivery window (like a 2-hour slot)
is standard for any real delivery service — customers plan around
being home, and a store needs to avoid promising more deliveries in
one window than it can actually fulfill.

**What it does:** Each org gets an admin-configurable daily operating
window, slot length, and per-slot order cap (`PATCH /admin/store/slot-settings`,
new "Delivery Time Slots" card in the Products page — defaults to a
9am-9pm day cut into 2-hour slots, 10 orders max per slot). A new
`GET /stores/{org_id}/delivery-slots?date=...` endpoint generates the
bookable windows for a given day (today plus up to 6 days out),
already accounting for how many paid orders are booked into each —
past slots for today are automatically excluded. Checkout accepts an
optional `slot_start`, re-validated server-side against the exact same
generation logic that produced the options the customer saw (so a slot
shown as available can never be rejected, and a stale/tampered/full
one always is), and copies the resolved window onto both the Order and
the Delivery record it creates. `Storefront.jsx` got a date-tab + time-
slot picker in the checkout form, showing remaining capacity per slot;
skipping it entirely still works exactly as before (ASAP delivery).

---

## Smarter / Automated Agent Assignment

**What was missing:** A dispatcher assigning an order picked from a
plain alphabetical list of agents, with no guidance — despite live
agent GPS positions already being collected for the customer tracking
map and just sitting unused for this decision.

**Why it was needed:** The whole point of already tracking agent
locations is exactly this: helping a dispatcher assign the delivery to
whoever can actually get there fastest, instead of a blind pick.

**What it does:** New `GET /deliveries/{id}/suggested-agents` ranks
every agent in the org for one specific delivery — nearest by live GPS
distance first (haversine straight-line against the delivery's
coordinates, reusing `AgentLocationDB`), with current active-delivery
workload as the tiebreaker. Falls back to workload-only ranking when a
delivery has no coordinates or an agent has never shared a location —
those agents still show up, just without a distance and sorted after
anyone who has one, so nobody silently disappears from consideration.
A new `POST /deliveries/{id}/auto-assign` uses the same ranking to
assign the top match in one click, sharing its actual assignment logic
with the existing manual "pick from the list" endpoint (refactored
into one `_apply_agent_assignment()` helper so both paths produce
identical history entries and notifications). `DispatcherTable.jsx`'s
unassigned-orders queue got a "🎯 Suggest agent" button that annotates
the agent dropdown with live distance/workload, and a "⚡ Auto-assign"
button for skipping the pick entirely.

---

## Conflict-Resolution Visibility

**What was missing:** The offline sync engine resolves conflicting
updates with last-write-wins (see `services/conflict_resolver.py`) —
but silently. If an agent's offline status change lost to a newer
change made elsewhere while they were offline, their update was just
discarded with no signal anywhere that it happened.

**Why it was needed:** Silently throwing away someone's real update is
a data-loss-adjacent problem — an agent who marked a delivery
"delivered" offline deserves to know if that never actually stuck,
rather than discovering it later (or never).

**What it does:** `resolve_and_apply()` now returns a conflict record
(not just the winning row) whenever an incoming offline change is
discarded in favor of a newer one already on the server — including,
where available, who made the winning change (looked up from the
existing delivery-history audit log). `POST /sync`'s response gained a
`conflicts` list carrying this alongside the usual `resolved_records`/
`errors`. `AgentDeliveryList.jsx` turns each one into a durable,
dismissible banner ("Order X: your change to '...' was overridden —
Y already updated it to '...' more recently") plus an immediate toast,
shown on both the periodic background sync and a manual "Sync Now" —
so a discarded change is now something the agent can actually see and
act on, not something that vanishes.

---

## Two-Factor Authentication (Staff Logins)

**What was missing:** A staff account (agent/dispatcher/admin) was
protected by a password alone. Anyone who obtained or guessed a
password had full account access — including an admin's ability to
manage every user in the organization.

**Why it was needed:** Trust & compliance requirement — staff logins
needed a second factor beyond the password, the standard baseline for
any account with administrative or operational access to real customer
data.

**What it does:** TOTP-based 2FA (RFC 6238 — the same standard behind
Google Authenticator, Authy, 1Password), free and self-contained since
it needs no SMS/email provider. `UserDB` gained `totp_secret` /
`totp_enabled`. Setup is two steps on purpose (`POST /auth/2fa/setup`
returns a QR code but doesn't enable anything; `POST /auth/2fa/enable`
only flips it on once a real code from the freshly-scanned app is
confirmed), so an abandoned setup can't lock an account out. Once
enabled, `POST /auth/login` no longer returns a session token directly
for that account — it returns a short-lived (5 min) `challenge_token`,
and `POST /auth/2fa/verify-login` exchanges that plus a correct code for
the real access token. `POST /auth/2fa/disable` requires the password
again, not just an active session. Frontend: `LoginPage.jsx` gained a
second step for the code; a new "Security" page (`TwoFactorSettings.jsx`,
in the sidebar for every staff role) handles setup/enable/disable, with
the QR rendered via the same free `api.qrserver.com` image API already
used for order QR codes.

---

## Audit Log Viewer (Admin)

**What was missing:** Delivery status changes were already recorded
with who/what/when (`DeliveryHistoryDB`, powering each delivery's
individual history timeline) — but there was no way for an admin to
browse that data broadly, across every delivery in the organization at
once.

**Why it was needed:** Trust & compliance requirement — admins need a
proper audit trail view, not just a per-delivery timeline they'd have
to click into one order at a time.

**What it does:** `GET /admin/audit-log` joins `DeliveryHistoryDB` to
`DeliveryRecordDB` (for org-scoping, since history rows don't carry
`org_id` directly) and returns every status-change entry for the
admin's organization, filterable by date range, who made the change,
and order ID, with pagination. New `AuditLogViewer.jsx` (sidebar, admin
only) renders it as a filterable table with a "Load more" pager.

---

## GDPR-Style Data Export & Account Deletion (Customers)

**What was missing:** A customer had no self-serve way to see everything
the platform held about them, or to delete their account.

**Why it was needed:** Trust & compliance requirement — a basic "right
to access" and "right to erasure" flow for customer accounts.

**What it does:** `GET /customer/data-export` streams a single JSON file
with everything tied to the logged-in customer: profile, saved
addresses, orders + line items, linked deliveries + status history +
feedback given, cart, notifications, product reviews, and registered
push-notification devices (endpoint only — private keys withheld, since
those are security credentials, not personal data worth exposing in a
downloadable file). `DELETE /customer/account` requires the password
again (not just an active session) and deletes purely personal data
outright (cart, addresses, notifications, push subscriptions) — but
_anonymizes_ rather than deletes orders/deliveries/reviews, since a
store has a legitimate business reason to retain its own transaction
and refund records even after a customer's account is gone, the same
pattern real e-commerce platforms (Amazon, Shopify) use. New
`PrivacyPanel` in `CustomerDashboard.jsx` (🔒 Privacy button) offers
both actions, with a password-confirmation step before deletion.

---

---

## Real Fix: `.env` Was Never Actually Being Loaded

**What was wrong:** Every "optional real, else console-log" integration
(SMTP email, Twilio SMS, Razorpay, VAPID) reads its config as a
module-level constant via `os.environ.get(...)`. Nothing in the codebase
ever called `load_dotenv()` — no `python-dotenv` dependency existed at
all. Filling in real values in `backend/.env` had **zero effect**: those
values only ever reach `os.environ` if something loads the file into
the process first, and nothing did. Password reset (and everything else
gated the same way) kept "printing instead of sending" no matter what
`.env` said.

**The fix:** Added `python-dotenv`; `main.py` now calls `load_dotenv()`
as the literal first lines of the file, before any local import — this
has to happen before those modules are first imported, since that's the
exact moment their module-level `SMTP_HOST = os.environ.get(...)`-style
constants get evaluated. Verified directly: `SMTP_HOST` reads as `None`
without the fix, and the real value with it.

---

## Agent Coverage Area (Real GPS Reverse Geocoding) + Zone-Based Assignment

**What was missing:** Suggested/auto-assign ranking used only live GPS
distance and workload — there was no concept of an agent's actual
coverage area, so a dispatcher couldn't assign based on "who actually
covers this zone."

**What it does:** New `services/geocoding.py` calls OpenStreetMap's free
Nominatim reverse-geocoding API (no key, no billing) to turn an agent's
real device GPS coordinates into a real area name (e.g. "Koramangala,
Bengaluru") — a genuine reverse geocode, not a hand-typed field.
`POST /users/me/area/detect` (agent-only) saves it; new "My Area"
section + "📍 Detect My Area" button in `AgentDeliveryList.jsx`.
`_rank_agents_for_delivery` now sorts zone-matched agents (delivery's
`zone` vs. agent's `area_name`, loose case-insensitive match) ahead of
everyone else, before distance or workload — a far-away agent whose area
matches the delivery's zone now outranks a much closer agent who doesn't
(verified directly: 1111km-but-zone-matched agent ranked #1 over a
0.78km-but-wrong-zone agent). Dispatcher's assign dropdown and suggested-
agents panel show each agent's area and a zone-match indicator.

---

## Email-Code Two-Factor Authentication (Second Method)

**What was missing:** 2FA only supported an authenticator app (TOTP).
Scanning the setup QR code with a phone's general camera or Google
Lens/Search doesn't work — those read it as plain text and try to
web-search it, since `otpauth://` isn't a scheme they know how to open.
That's expected behavior for those tools, not a bug, but it meant
anyone without a dedicated authenticator app installed had no way to use
2FA at all.

**What it does:** A second, independent 2FA method — a 6-digit code
emailed to the account's own address, real SMTP delivery (see the
`.env` fix above), reusing the same hashed-and-expiring code pattern as
password reset tokens (`models/email_otp.py`). Setup: `POST
/auth/2fa/setup-email` sends a confirmation code immediately (no QR —
the "device" being set up is the inbox itself); `POST
/auth/2fa/enable-email` confirms it. Login: when an "email"-method
account signs in, `POST /auth/login` sends a fresh code automatically
and returns `two_factor_method`/`masked_email` alongside the challenge
token; `POST /auth/2fa/resend-code` covers a lost/expired code.
`TwoFactorSettings.jsx` now offers both methods side by side, with
explicit instructions to use an authenticator app's own QR scanner
(not a camera app or Lens) for the TOTP option. Verified end-to-end:
setup → enable → login-auto-sends → wrong-code rejection → correct-code
success → single-use enforcement — and confirmed the existing TOTP path
is completely unaffected by an account using the email method.

---

---

## Real Bug Fix: Checkout Crash Misreported as "You're Offline"

**What was wrong:** A misconfigured (or just-invalid-test) Razorpay key
pair made `create_razorpay_order()` raise `BadRequestError:
Authentication failed`, uncaught, deep in `POST /customer/checkout`.
Combined with how Starlette's `@app.middleware("http")` pattern handles
an unhandled exception, the browser's `fetch()` saw the connection drop
rather than a clean error response — which the checkout code's
`err instanceof TypeError` catch (reasonably, at the time) treated as
"must be offline," silently queuing the order instead of surfacing the
real problem. The customer saw "You're offline" while genuinely online.

**The fix, at both ends:**

- `routes/checkout.py` now wraps the Razorpay call in a real
  try/except — an authentication failure returns a clean `502` with an
  actionable message (check `RAZORPAY_KEY_ID`/`SECRET` in `.env`, or
  unset them to use the built-in test-mode path) instead of crashing.
- `main.py`'s security-headers middleware is now a backstop for _any_
  unhandled exception anywhere in the app — logs it server-side and
  always returns a clean `500 {"detail": "..."}` JSON response, so a
  future bug in a completely different route can never again show up in
  the browser as a misleading "offline" state. Verified directly: a
  forced, unrelated exception in an unrelated route now returns a clean
  500 instead of dropping the connection.
- `Storefront.jsx`'s offline-detection now checks `navigator.onLine`
  before treating a failed fetch as "offline" — a `TypeError` while
  actually online now shows "Couldn't reach the server" instead of
  silently (and incorrectly) queuing the order as if offline.

---

## Real Cash-on-Delivery (COD) Checkout

**What was missing:** Checkout was online-payment-only (Razorpay, or a
test-mode stand-in when no gateway is configured) — no way for a
customer to choose "pay in cash when it arrives."

**What it does:** `CheckoutRequest.payment_method` — `"online"` or
`"cod"`. Choosing COD skips Razorpay entirely: the order is confirmed
immediately (no separate payment-verification round trip needed beyond
the existing test-mode-style `POST /checkout/verify` call), stock is
decremented, a `DeliveryRecordDB` is created and lands straight in the
dispatcher's unassigned queue exactly like an online order — verified
end-to-end. Cancelling a COD order before delivery correctly skips any
refund attempt in `services/refund.py` (nothing was ever charged) and
just restocks. `Storefront.jsx` gained a Pay Online / Cash on Delivery
toggle in the checkout form, with matching copy in both the immediate
and the offline-queued-then-synced confirmation messages.

---

## Config Fix: `FRONTEND_URL` Port Mismatch (Password Reset Links)

**What was wrong:** `frontend/vite.config.js` runs the dev server on
port **3200**, but `backend/.env.example`'s `FRONTEND_URL` example value
said **5173** (Vite's own default, not what this project actually
uses). Copying `.env.example` to `.env` without editing that line means
every password-reset email links to a port nothing is listening on —
"this site can't be reached."

**The fix:** `.env.example` now matches the real port (3200) with a
comment explaining why it has to match whatever `npm run dev` actually
prints, rather than assuming Vite's default.

---

---

## Real Bug Fix: Existing Databases Broke on Every New Column

**What was wrong:** `Base.metadata.create_all(bind=engine)` (the only
schema setup this project had) only creates tables that don't exist
yet — it silently does NOT add new columns to a table that already
exists from an earlier run. The COD feature added `payment_method` to
`OrderDB`, and anyone with an existing local `database.db` file (i.e.
anyone who'd actually been using the app) hit `sqlite3.OperationalError:
table orders has no column named payment_method` on the very next
checkout — a hard crash, not a graceful degradation. This wasn't
specific to `payment_method`; the exact same failure was waiting to
happen on every future column added to any existing table, for anyone
with real data already in their database.

**The fix:** New `app/db/migrate.py` — a lightweight, dependency-free
migration step (this project deliberately doesn't use Alembic, in
keeping with its zero-setup philosophy) that runs right after
`create_all()` on every startup: it diffs each table's actual columns
against what the model currently expects, and `ALTER TABLE ... ADD
COLUMN`s in whatever's missing. New columns are always added nullable
regardless of the model's own `nullable=False`, specifically so it can
never fail against a table that already has rows. Verified directly
against Santy's exact scenario: hand-built an old-schema `orders` table
(matching the schema from before `payment_method` existed) with a real
row in it, ran the app's real startup against it, confirmed the column
got added, the existing row survived untouched, AND SQLite correctly
backfilled it to the column's default (`'online'`) — then ran a live
COD checkout against that same migrated database end-to-end and
confirmed it completes. Also confirmed a totally fresh install (no
`database.db` at all) still works with zero regressions. This means an
existing local database now survives every future schema change this
project makes, without ever needing to be deleted and started over.

---

---

## Real Bug Fix: "My Orders" Showing on Every Customer Page

**What was wrong:** The customer dashboard's Shop/Addresses/Privacy
sections were each an independent toggle-boolean that could all be open
simultaneously, and "My Orders" wasn't gated behind any of them at all —
it just always rendered underneath whatever else was open, so it showed
up no matter which button you'd clicked.

**The fix:** Replaced the three separate booleans with one `activeView`
tab state ("orders" | "shop" | "addresses" | "privacy" | "profile"), so
exactly one section is visible at a time, with the active tab visually
highlighted. "My Orders" is now genuinely its own page.

---

## Checkout Now Uses Saved Addresses

**What was missing:** Saved addresses (added earlier for multi-address
profiles) were never actually connected to checkout — there was no way
to pick one, and nothing prefilled.

**What it does:** `Storefront.jsx` loads saved addresses on open and
auto-fills the checkout form from the default (or the only) saved
address. A dropdown above the address field lets you switch between any
saved address or "enter a new address" instead — editing the fields
directly also switches it to "new" automatically so it's clear you're
not silently overwriting a saved address.

---

## Agent Area: Manual Selection, Not Just GPS

**What was missing:** The only way to set an agent's coverage area was
GPS detection — no way to pick or type an area directly (useful when
the reverse-geocoded name doesn't match what dispatchers actually call
a zone, or GPS just isn't available/accurate).

**What it does:** New `POST /users/me/area/set` sets an area directly by
name (no coordinates — there's no real GPS fix behind a manually-typed
area, and zone-matching only ever compares names anyway). New `GET
/users/me/area/suggestions` returns area names already in use across
the org (other agents' areas + zones dispatchers have typed onto
deliveries), so picking an area can be "choose from what's already used
here" via a dropdown, with a free-text box alongside it for anything
new. `AgentDeliveryList.jsx`'s area section now offers all three: GPS
detect, pick-from-list, or type-your-own.

---

## Customer Profile + Notification Cleanup

**What was missing:** No way for a customer to view/edit their own
name or email, or change their password — genuinely no profile page
existed at all. Also no way to delete notifications; they only ever
accumulated.

**What it does:**

- New `GET/PATCH /customer/me` (name/email, with email-uniqueness
  checking) and `POST /customer/me/change-password` (requires the
  current password, same re-auth-to-change-something-sensitive pattern
  used for staff 2FA disable). New "👤 Profile" tab with both forms.
- New `DELETE /customer/notifications/{id}` (single) and `DELETE
/customer/notifications?only_read=true/false` (bulk — defaults to
  only clearing already-read ones, the safer default for a "clean up"
  action, with a full-clear option available). Notification panel
  gained a 🗑 delete button per item and a "Clear read" button.

---

## (Template for future entries — copy this structure)

## Feature Name

**What was missing:**

**Why it was needed:**

**What it does:**
