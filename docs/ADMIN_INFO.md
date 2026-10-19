# Admin Info — features, access, and what every menu option does

This guide is for the **admin** of a Delivery Sync organization. It explains what an admin can do, how that differs from a dispatcher and an agent, and then walks through **every option in the admin sidebar**, in the same order and grouping you see on screen.

> Everything described here is based on what the code actually does (`backend/app/routes/*`, `backend/app/models/*`, `frontend/src/components/*`). Where a feature has a limit or a rule that surprises people, it is called out under **Good to know**.

---

## 1. Who is the admin?

- The **first person to sign up for a new organization becomes its admin**, whatever role they picked. Signing up also creates the organization.
- The admin receives a **team invite code** (shown after signup, included in the signup email, and always visible at the top of **Manage Users**). Agents and dispatchers join the organization by entering that code on the Sign Up screen.
- A new person who joins with the invite code **cannot make themselves an admin** (the server refuses it). Admin is only obtained by creating the organization.
- **Every organization is completely isolated.** All data (deliveries, users, products, orders, settings) is filtered by the organization of the signed-in user. An admin can never see or change another organization's data.

### Roles at a glance

| | **Agent** | **Dispatcher** | **Admin** |
|---|---|---|---|
| Purpose | Delivers parcels in the field | Runs day-to-day operations | Owns and configures the organization |
| Sees their own deliveries | Yes | — | — |
| Sees all deliveries, assigns agents | No | Yes | Yes |
| Manages products, coupons, store settings | No | Yes | Yes |
| Warehouses/stock operations | No | Yes (default permission) | Yes |
| Views finance / reconciliation | No | View only (default) | Full |
| Analytics and Advanced Analytics pages | No | No | **Yes (admin only)** |
| Manage users, roles, zones, reason codes, POD rules, SLA policies, RTO settings | No | No | **Yes (admin only)** |
| API keys & webhooks | No | Yes | Yes |
| Organization settings, monitoring, audit log | No | No | **Yes (admin only)** |

Admins **bypass all permission checks**: an admin can do anything inside their own organization.

### The permission system (custom roles)

Besides the three base roles, an admin can create **custom roles** with an exact list of permissions and assign one to any non-admin user (see *Custom Roles* below). The permission catalog is:

| Area | Permissions |
|---|---|
| Deliveries | `deliveries.view`, `deliveries.create`, `deliveries.assign`, `deliveries.update`, `deliveries.cancel`, `deliveries.delete` |
| Users | `users.view`, `users.manage` |
| Inventory | `inventory.view`, `inventory.manage` |
| Payments | `payments.view`, `payments.manage`, `payments.refund` |
| Analytics | `analytics.view`, `analytics.export` |
| Workforce | `workforce.view`, `workforce.manage` |
| Settings | `settings.view`, `settings.manage` |

**Default permissions when a user has no custom role**

- **Agent:** `deliveries.view`, `deliveries.update`, `workforce.view`
- **Dispatcher:** `deliveries.view/create/assign/update/cancel`, `inventory.view/manage`, `payments.view`, `analytics.view`, `workforce.view/manage`, `settings.view`
- **Admin:** everything

**Rule:** once a user has a custom role, that role's list is the *only* thing that applies (even if it grants less than their base role). Enforcement is on the server, so hiding a button in the UI is never the only protection.

---

## 2. What can an admin do? (summary)

**Run operations**
- See every delivery, create deliveries, assign them manually or automatically, move them between agents, return them to the unassigned pool, reschedule, set priority, change many at once, import many from a CSV, and export deliveries to CSV.
- Set delivery-time targets (**SLA**), watch what is at risk, and manage **Return-to-Origin (RTO)** and customer **returns & exchanges**.
- Plan routes and see delivery hotspots.

**Manage people**
- See all users, deactivate/reactivate accounts, set a new password for someone, and give people **custom roles**.
- Manage **shifts, attendance, leave, and pay statements** for staff, and track **vehicles** (maintenance, fuel, utilization).

**Run the store and money**
- Manage products (one at a time or **import from CSV**), coupons, delivery fee and GST, delivery time slots, and whether the public storefront is visible.
- Manage warehouses, stock, suppliers, and purchase orders.
- Reconcile cash-on-delivery, record settlements, view the payment ledger, and issue invoices, credit notes and debit notes.
- Handle customer support tickets.

**Configure the organization**
- Define **zones**, **failed-delivery reason codes**, **proof-of-delivery** requirements, **customer notification templates**, branding, timezone and currency.
- Create **API keys** and **webhooks** for integrations.
- Suspend or reactivate the organization, and export the organization's data.

**Stay in control**
- Read the **audit log**, the **monitoring** page (health, errors, backups, email delivery), and secure your own account with **2FA**.

---

## 3. The sidebar, option by option

The admin sidebar is grouped. Each section below says what the page is for, what you can do on it, and what to watch out for.

### Group: Overview

#### Dashboard
The operations home page (shared with dispatchers).
- **Summary stats** for the organization's deliveries.
- **Assign a New Delivery**: create a delivery record and give it to an agent.
- **Unassigned Orders**: customer orders and deliveries waiting for an agent.
- A **delivery list** with filters (status, agent, date range), search, sorting, pagination, and a click-through **detail view** (history of status changes, delivery attempts, proof of delivery).
- **Select many** rows to change status or assign an agent in bulk.
- **Auto-assign / suggested agents**: the system suggests agents; if a delivery's address falls inside a **zone**, only agents covering that zone are considered.
- **Return to pool**, **reschedule**, and **change priority** on a delivery.
- **Export CSV** (can be limited to a date range) and **Bulk import** deliveries from a CSV.

*Good to know:* every delivery belongs to your organization; assignments, status changes and imports are recorded for the audit log.

#### Analytics (admin only)
A business overview calculated live from your real orders and deliveries:
- **Delivery status breakdown**, **order volume**, and **fulfillment** numbers.
- **Revenue** over the last N days and **Top products by revenue**.
- **Low stock alerts**.

#### Advanced Analytics (admin only)
Deeper reporting: **agent productivity**, **failed delivery reasons**, **return requests**, **revenue by category** and **by payment method**, plus retention, margin and trend figures.

---

### Group: Operations

#### Routing
Org-level routing insights (admin and dispatcher):
- **Delivery heatmap**: where deliveries cluster.
- **Multi-agent route optimization**: trigger an optimized split of stops across agents.
- Per delivery (from its detail view): **ETA**, **route deviation**, **route replay** and **route efficiency**.

*Requires:* `deliveries.view` to view, `deliveries.assign` to run optimization.

#### SLA
Delivery-time targets and what is at risk.
- **At Risk / Breached Now**: deliveries about to miss, or already past, their target.
- **By Agent** and **By Zone** analytics.
- **SLA Policies** (admin only): create, edit, delete. A policy has a **name**, a **target time in minutes**, a **warning threshold percentage**, an **active** switch, and optional scope by **zone**, **delivery type** and **priority**.

*How a policy is chosen:* for each delivery the system picks the single best-matching active policy; the more specific one wins (zone + type + priority beats zone + priority beats zone only beats the organization-wide default with nothing set). The target time is counted from when the delivery **entered the system**.

#### RTO (Return to Origin)
For a delivery that **could not be completed** and must come back to you (different from a customer return of something already received).
- A request is created **automatically** when a delivery becomes eligible, i.e. it reaches the allowed number of failed attempts (default **3**; admin can change it here).
- Lifecycle: **Eligible → Approved → In transit → Received at origin**, or **Cancelled** (you decide to reattempt instead).
- Buttons move a request to its next step; **By Reason** analytics show why deliveries fail.

*Requires:* `deliveries.view` to see, `deliveries.assign` to act. Changing the attempt limit is admin only.

#### Returns & Exchanges
The review queue for customers who want to send back (or swap) a **delivered** order.
- **Approve** creates a real **return pickup delivery** in the normal unassigned queue, ready to assign to an agent. When that pickup is marked delivered (back at your store), the request completes automatically: a **return** triggers a refund, an **exchange** creates the replacement delivery.
- **Reject** closes it with a note to the customer.
- Customers get notified using the *return approved* template (see Notifications).

#### Zones (admin only)
Delivery territories. A zone is a **circle** (center latitude/longitude + radius in km); there is a **Use my current location** shortcut. Assign the **agents who cover** each zone.
- A delivery whose coordinates fall inside a zone is auto-assigned **only to that zone's agents**.

#### Reason Codes (admin only)
The standard list of reasons an agent picks when a delivery attempt **fails** (e.g. "Customer unavailable", "Wrong address").
- **Enforced:** an agent cannot mark a delivery as failed without an active reason, so your attempt history is always clean and usable.
- Reasons can be turned **inactive** instead of deleted, so old records keep working.

#### Proof of Delivery (admin only)
Choose what an agent must capture before a delivery can be marked **Delivered**. Four switches, all **off by default**:
- recipient name, signature **or** photo, **OTP** code, GPS location.
- Also offers a downloadable **POD report (CSV)**.

---

### Group: Resources

#### Warehouses
Inventory across one or more physical locations. Tabs: **Warehouses**, **Inventory**, **Suppliers**, **Purchase Orders**, **Low Stock**.
- **Record stock movements**: Stock In, Stock Out, Set (adjust), Transfer between warehouses, Report Damage. A **Movement History** is kept.
- **Purchase orders**: create orders to suppliers and **receive** items against them.
- Warehouse create/edit/delete is **admin only**; day-to-day stock work needs `inventory.manage` (dispatchers have it by default).
- Warehouse stock and the store's product stock are separate numbers; there is an explicit **sync** action to push a warehouse total into a product's stock.

#### Fleet
Your vehicles: add vehicles, **assign** one to an agent, record **inspections**, **maintenance** and **fuel**, see **utilization (30 days)** and an **Attention needed soon** list (insurance, registration, inspection and service due dates).
- A vehicle's live location comes from its assigned agent, not a second tracker.
- Agents only see **their own** assigned vehicle.

#### Workforce
Staff scheduling and pay. Tabs: **Shift Roster**, **Attendance Log**, **Leave Requests**, **Earnings**.
- Create and edit **shifts**; agents **clock in and out** themselves and the **attendance log** shows it.
- **Approve or reject leave** requests.
- **Set Pay Rate** per person, **Generate Earnings** statements for a period, then **Finalize** and **Mark Paid**.

#### Products
Your catalog and storefront settings (admin and dispatcher).
- **Products**: add one at a time, edit, delete, upload images, set price, cost price, stock, category, active/inactive.
- **Import from CSV**: add many at once (see section 4).
- **Coupons**: create promo codes, switch them on/off, change limits.
- **Delivery Fee & Tax (GST)**: the fee and tax rate applied at checkout.
- **Delivery Time Slots**: slot length, daily window, and the maximum orders per slot.
- **Marketplace Listing** and **Public Storefront**: your store's profile (category and description) and whether the storefront is visible to the public.
- Changes to products, coupons and store settings are recorded in the Admin Actions audit log.

---

### Group: Finance & Support

#### Finance (Reconciliation)
Money tracking for orders. Tabs: **Overview**, **COD Collections**, **Settlements**, **Ledger**.
- **COD collections:** compare what an agent **should** have collected on a cash-on-delivery order with what they **did** collect, and spot discrepancies.
- **Settlements:** group collections and **Mark Settled** when the cash is handed in.
- **Ledger:** an **append-only** record of every financial event (charge, refund, COD collection, discrepancy, settlement). Nothing is edited or deleted; corrections are new rows.
- *Requires:* `payments.view` to look, `payments.manage` to create/settle.

#### Invoicing
Financial documents: **invoices, receipts, refund receipts, credit notes, debit notes**.
- Filter by type and **download PDFs**.
- Create a **credit note** (reduce what a customer owes) or a **debit note** (e.g. a cash-on-delivery shortfall) manually.
- An **organization financial report** reuses the reconciliation figures so the numbers always agree.
- Invoices and refund documents are generated automatically at checkout and refund time and snapshot the amounts, so they never change later.

#### Support
Customer support tickets (admin and dispatcher).
- Triage: change **status**, **priority**, **assign** to a staff member, write **internal notes** (staff only), reply to the customer, and **Resolve**.
- Tickets can reference an order or delivery; customers can attach files.
- A support **analytics** view summarizes volume and resolution.

#### Notifications (admin)
Customize the wording of customer messages per event, and choose the channels (email, SMS, WhatsApp, in-app). Events: **refund processed, return approved, agent nearby, delivery reminder, subscription reminder**.
- Every event starts with a built-in default; **Reset to Default** removes your customization.
- *Requires:* `settings.manage` to edit.

---

### Group: Administration

#### Manage Users (admin only)
Everyone in your organization.
- Shows your **team invite code** at the top (share it so agents and dispatchers can join).
- **Deactivate / reactivate** accounts (a deactivated person cannot log in).
- **Reset Password**: you type a new password and tell the person yourself (it is not emailed).
- **Assign a custom role** from the dropdown on each non-admin row ("Default" means their base role's permissions).

#### Custom Roles (admin only)
Create named roles with a chosen set of permissions (**+ New Role**), edit them, delete them. Assigning a role to a person is done in **Manage Users**.

#### Organization (admin only)
- **Branding:** logo and brand color.
- **Locale:** timezone, currency code and symbol.
- **Usage:** how much the organization is using the system.
- **Pause Operations:** **Suspend** or **Reactivate** the organization. A suspended organization cannot be joined with its invite code.
- **Data Export:** download your organization's data.

#### API & Webhooks (admin and dispatcher)
Connect other systems.
- **API keys:** create with **scopes** (`deliveries:read`, `deliveries:write`, `orders:read`, `webhooks:manage`). The key is shown **once**; afterwards only a prefix is visible. **Rotate** or **Revoke** at any time. External systems call `/api/v1/deliveries` and `/api/v1/orders` with the header `X-API-Key`.
- **Webhooks:** register a URL and choose events: `delivery.created`, `delivery.assigned`, `delivery.picked_up`, `delivery.out_for_delivery`, `delivery.delivered`, `delivery.failed`, `order.created`, `order.paid`, `order.cancelled`, `refund.created`, `return.created`.
- A **Delivery Log** shows each attempt, and you can **Replay** a failed one.

---

### Group: System & Account

#### Monitoring (admin only)
Is the system healthy?
- **Health** of the app and database, and **Background Job Heartbeats** (are the scheduled jobs running).
- **Slowest endpoints** and API metrics.
- **Notification Delivery:** counts of emails/SMS sent or failed, with a **"Last problem"** line that explains a failure in plain words (for example "Login was rejected" or "SMTP_HOST isn't set"). Start here if emails are not arriving.
- **Recent Errors** from the server.
- **Database Backups:** create a backup, list backups, and **Verify** one.

#### Audit Log (admin only)
Two tabs:
- **Delivery Status Changes:** who changed a delivery's status and when.
- **Admin Actions:** user management (deactivate, reactivate, password reset), product, coupon and store-setting changes, product CSV imports. Filters and **Load more**.

#### My Account
Edit your name and email, **change your password** (or **set one** if you signed up with Google), and **Delete Account**.
- You cannot delete your account if you are the **only admin** and other people are still active in the organization (deactivate them first). If you are working alone, deleting your account also **closes the organization**.

#### Security
- **Two-factor authentication:** authenticator app (scan the QR code) or an emailed code; **recovery codes** are shown once.
- **Active Sessions** (revoke one or **Log Out All Devices**), **Login History**, **Security Activity**, **Recovery Codes** (regenerate).

---

## 4. Importing products from CSV

Products → **Import from CSV**.

1. Click **Download template** (or use `sample-data/sample_products.csv`).
2. Fill it in. Required columns: `name`, `price`. Optional: `description`, `category`, `image_url`, `stock_quantity`, `cost_price`, `is_active` (yes/no).
3. Choose the file, review the preview, and click **Import**. Up to **1,000 products** per file.

Rules: prices may be written like `₹1,299.50`; stock must be a whole number; `image_url` must start with `http://`, `https://` or `/uploads/`. Each row is checked on its own, so one bad row is reported with a reason while the others still import. Products are matched by **name** (ignoring case): existing names are skipped, or **updated** if you tick "Update products that already exist" (blank cells keep the old value).

---

## 5. Things to know / limits

- **Password resets are manual:** *Reset Password* sets a new password directly; you tell the person. (Normal "forgot password" emails exist for users themselves.)
- **Email:** welcome, verification codes and password resets are only emailed once SMTP is configured in `backend/.env`; otherwise they are printed in the backend console. See the README's "Real emails" section.
- **Verification does not block login:** a new account can use the app before verifying its email; verification is by a 6-digit code.
- **Offline sync is authenticated:** agents' offline changes are accepted only for deliveries assigned to them; dispatchers/admins can sync within their own organization (see `SECURITY_AND_ACCESS.md`).
- **Stock numbers:** product stock (used at checkout) and warehouse stock are separate until you use the sync action.
- **Suspending the organization** pauses it and blocks new joiners; **reactivate** to resume.
- **Deleting your own admin account** is blocked while others still depend on the organization (see *My Account*).

---

## 6. First-day setup checklist for a new admin

1. **Organization:** set branding, timezone and currency. (Your invite code is at the top of **Manage Users**.)
2. **Invite your team:** share the code; agents/dispatchers sign up with it.
3. **Reason Codes:** review/add the failed-delivery reasons.
4. **Zones:** define territories and assign covering agents.
5. **Proof of Delivery:** decide what agents must capture.
6. **SLA:** add an organization-wide policy, then specific ones.
7. **Products:** add or **import** your catalog; set delivery fee, GST and time slots; add coupons.
8. **Warehouses & suppliers** (if you track stock).
9. **Notifications:** adjust customer message wording.
10. **Security:** turn on 2FA for your own account; set up **SMTP** so emails go out.
11. **Monitoring:** confirm health, jobs and email delivery look good.
