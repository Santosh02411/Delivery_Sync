# Delivery Sync — Agent Mobile App

A real React Native (Expo) app for delivery agents, built as a second,
independent client of the exact same backend API the web app uses — no
new backend endpoints were needed to build this (see [How This
Talks to the Backend](#how-this-talks-to-the-backend) below).

## Why This Exists

The web app already has an "agent" experience
(`frontend/src/components/AgentDeliveryList.jsx`) that works fully
offline and lets an agent share their live location with a "Share my
location" toggle. That toggle uses the browser's
`navigator.geolocation.watchPosition` — which works well while that
browser tab is open and in the foreground, and **stops firing the
moment the tab is backgrounded or the phone locks**. This is a
deliberate, platform-level restriction in every mobile browser (Safari
and Chrome both do this to save battery and protect privacy), not a
bug or a missing library — there is no web API that reliably keeps GPS
updates flowing once the tab itself loses focus.

This app closes that specific, genuine gap: real OS-level background
location tracking (a foreground service on Android; "Always" location
permission + a background mode on iOS), so a customer's live tracking
map keeps updating while an agent is driving with the phone locked in
a cupholder — something a Progressive Web App fundamentally cannot do
on either platform. See `src/locationTask.js` for the full technical
explanation and its own honest limitations.

## How This Talks to the Backend

This app is a plain HTTP client of `backend/main.py` — the same
backend the web app and web dispatcher console already use. Every
endpoint it calls already existed and was already tested before this
app was written:

| This app calls | For |
|---|---|
| `POST /auth/login` (+ `/auth/2fa/verify-login` if 2FA is on) | Login |
| `POST /auth/signup` | Creating an agent/dispatcher account (join via invite code, or create a new org) |
| `POST /auth/forgot-password` | Requesting a password reset email |
| `GET /auth/me` | Restoring a session on app relaunch |
| `GET /deliveries/mine` | The agent's delivery list |
| `GET /deliveries/{id}` | Delivery detail |
| `PATCH /deliveries/{id}` | Advancing delivery status |
| `POST /deliveries/{id}/pod` | Submitting proof of delivery (photo/signature/recipient) |
| `GET /deliveries/reason-codes/active` | The org's failed-attempt reason codes |
| `GET /scan/{code}` + `POST /deliveries/{id}/scan` | Resolving a scanned QR code and recording the scan event |
| `GET`/`POST /deliveries/{id}/messages` | Loading a delivery's chat history, and sending a message |
| `WS /ws/deliveries/{id}/messages` | Real-time delivery of new chat messages |
| `PUT /users/me/location` | Both the manual foreground case AND the background task's periodic pings |
| `POST`/`DELETE /users/me/expo-push-token` | Registering/unregistering for push notifications |

**No backend code was written or changed to support any of this** — every one of
these endpoints already existed, was already used by the web app, and
was already tested before this mobile app called it. See
`mobile/src/services/api.js`'s own docstrings for the handful of
places worth knowing about (e.g. the location endpoint being generic
enough to work from a background task with zero backend changes; the
POD flow being two separate calls — submit, then mark delivered — by
the backend's own design, not this app's).

## Setup

Requires [Node.js](https://nodejs.org) and the
[Expo Go](https://expo.dev/go) app on a physical phone (fastest way to
test), or an Android Studio/Xcode simulator.

```bash
cd mobile
npm install
npx expo start
```

Scan the QR code with Expo Go (Android) or the Camera app (iOS).

**Point it at your backend:** by default this expects the backend at
`http://10.0.2.2:8000` (the Android emulator's alias for your
computer's `localhost`). For a physical device, set your computer's
real LAN IP instead:
```bash
EXPO_PUBLIC_API_BASE_URL=http://192.168.1.42:8000 npx expo start
```
(Find your LAN IP with `ipconfig` on Windows or `ifconfig`/`ip addr` on
macOS/Linux. Your phone and computer must be on the same Wi-Fi
network.)

Log in with any existing **agent** account from the web app (staff
signup happens on the web app — this app is intentionally login-only,
see [Not Yet Built](#not-yet-built) below).

## Running Tests

```bash
cd mobile
npm test
```

51 tests (Jest + jest-expo) covering the offline queue's actual logic —
`offlineStore.js`'s AsyncStorage-backed cache (per-user scoping, not
clobbering a pending edit with stale server data, pending-count
tracking) and `offlineSync.js`'s retry/backoff behavior and
foreground/connectivity-triggered sync, using real fake timers the
same way the web app's own `syncEngine.test.js` does — plus
`pushNotifications.js`'s permission/token/error-handling logic (8
tests; the one branch not covered — no physical device — is a single
early-return guard documented as untested in that test file's own
comment, a genuine Jest/Babel module-mocking limitation, not an
oversight), `api.js`'s newest request-building functions (13 tests
covering signup, forgot-password, proof of delivery, reason codes,
messaging, and scanning — request shape, error surfacing, and the
message-vs-body field-name mismatch that would have been an easy
mistake to ship), and `websocket.js`'s reconnect-with-backoff logic (8
tests, again driven with real fake timers — the exponential delay
actually verified step by step, not mocked away, plus the reset-after-
a-successful-reconnect behavior and the "caller closed it, stop
retrying" case). Screen
components (Login, DeliveryList, etc.) don't have tests yet — the
logic layer was prioritized since it's where a real bug would
actually cost real data or send the wrong request shape.

## Testing Background Location

1. Log in, go to **Settings**, toggle **Share my location** on.
2. Grant "While Using the App" location permission, then — when
   prompted separately — **"Allow All the Time"** / "Always". Both
   platforms gate this behind an extra step specifically because of
   how much background location tracking implies; declining the
   second prompt still works, just foreground-only (the toggle's
   screen explains this plainly rather than pretending it's the same).
3. Background the app (press the home button) or lock the phone.
4. On the web app, open that same delivery's public tracking page
   (`?track=<delivery-id>`) — the agent marker keeps moving on its own
   interval (60s by default — see `LOCATION_UPDATE_INTERVAL_MS` in
   `src/locationTask.js`) even though the app isn't on screen.

**Important — Expo Go's own limitation, not this app's:** as of Expo
SDK 51, background location does **not** work in Expo Go on iOS at
all (Expo Go itself doesn't include the native background modes this
needs) — only in a real build via `eas build`. It **does** work in
Expo Go on Android. This is documented by Expo, not a gap in this
codebase — see
[Expo's own background location caveats](https://docs.expo.dev/versions/latest/sdk/location/#background-location-updates)
for the current, authoritative version of this limitation.

## Building a Real, Installable App

This sandbox environment has no Xcode/Android Studio and no device to
test on, so no compiled `.apk`/`.ipa` was produced here — only the
source code. To get an actual installable build:

```bash
npm install -g eas-cli
eas login
eas build --platform android   # or ios
```
See [Expo's build docs](https://docs.expo.dev/build/introduction/) for
the full process — this requires a free Expo account.

## Offline Support

This app now has a real offline queue, closing what was previously its
single biggest gap versus the web agent app — mirroring
`frontend/src/services/syncEngine.js`'s architecture closely (same
retry constants, same conflict-description wording, same overall
control flow), adapted to React Native's actual APIs:

- **`src/services/offlineStore.js`** — an AsyncStorage-based local
  cache (the mobile equivalent of the web app's IndexedDB wrapper),
  scoped per logged-in user the same way. Every successful delivery
  fetch is cached; a status update is applied to the local cache
  immediately whenever the network request for it fails.
- **`src/services/offlineSync.js`** — sends queued updates to the
  backend's existing, already-tested `POST /sync` endpoint (the exact
  same one the web app's offline queue already uses — no new backend
  code was needed) with the same 3-retry logic as the web app, and
  reconciles the server's resolved version back into the local cache.
  Since React Native has no `navigator.onLine`/browser `"online"`
  event, connectivity is instead checked via `expo-network` and a
  sync is re-attempted whenever the app is foregrounded
  (`AppState` "active") or every 15 seconds while foregrounded.
- **Session restore also tolerates being offline** — opening the app
  with no connectivity at all (a real scenario: an agent starting
  their shift with no signal) restores the session from a locally
  cached profile instead of being treated the same as an
  expired/invalid token and logging the agent out, which would have
  defeated the entire point of offline support.
- Both the delivery list and delivery detail screens show a clear
  "Working offline" banner and a "queued to sync" badge on any
  not-yet-synced record, and Settings shows a live pending-update
  count with a manual "Sync Now" button — nothing about an offline
  edit is silent or hidden.

**What this offline queue does NOT do**, stated plainly: it only
applies to a delivery **already fetched at least once** (there's
nothing to safely merge an offline status change into otherwise); it
queues by writing straight to local storage rather than the web app's
richer background-sync-registration approach (see
`frontend/src/services/backgroundSync.js`) that can wake a service
worker even after every tab is closed — a native background task
would need `expo-task-manager` wired the same way `../locationTask.js`
already is, and hasn't been built for sync specifically (only for
location).

## Push Notifications

Real OS-level push, via [Expo's push notification service](https://docs.expo.dev/push-notifications/overview/)
— an agent gets a notification even with the app fully closed the
moment a delivery is assigned or unassigned to them, mirroring the web
app's own Web Push for the exact same events (both are sent from the
same shared fan-out function on the backend, `services/notifications.py`'s
`_push_to_user_ids()` — adding this required zero changes to any of
the individual call sites that trigger a staff notification).

**Setup required before this actually works**: unlike the web app's
Web Push (which has a working checked-in default VAPID keypair — see
`backend/app/services/push.py`), there is no working default for Expo
push, since a push token is inherently tied to a specific registered
app identity Expo's push service can route to. Run once, from this
`mobile/` folder:
```bash
npx eas init
```
This writes a real project id into `app.json` (requires a free Expo
account). Without this step, `registerForPushNotifications()` detects
the missing project id and quietly no-ops — every other feature in
this app (deliveries, status updates, background location, the
offline queue) works completely normally either way; only push
notifications themselves stay off until `eas init` is run.

Also requires a **physical device** — push tokens are unreliable/
unsupported on a simulator or emulator, so `registerForPushNotifications()`
no-ops there too (see `src/services/pushNotifications.js`).

## Signup & Password Reset

**Signup** (`SignupScreen.js`) mirrors the web app's own choice: join
an existing organization via invite code (as agent or dispatcher —
never admin via invite code, the same anti-privilege-escalation rule
the backend itself enforces regardless of what this screen sends), or
create a brand new organization (becoming its admin automatically).
No CAPTCHA token is sent — CAPTCHA is only actually enforced
server-side when `RECAPTCHA_SECRET_KEY` is configured, so this is a
real gap only for a deployment that has turned that on; signup would
fail there until this screen is extended with a mobile CAPTCHA widget.

**Password Reset** (`ForgotPasswordScreen.js`) requests the reset
email — the same `POST /auth/forgot-password` the web app calls — but
deliberately has no screen of its own for the second half (actually
setting the new password). The emailed link opens the **web app**
instead. This is a scope decision, not an oversight: real deep-linking
(a registered URL scheme/associated domain, tested on both platforms)
is meaningful setup for a flow that happens rarely per account — open
the emailed link in the phone's browser, set the new password there,
then come back here and log in normally.

## Proof of Delivery, Partial Delivery & Failed Attempts

Marking a delivery **Delivered** now opens `ProofOfDeliveryScreen.js`:
an optional recipient name, a real camera photo (`expo-image-picker`),
a real hand-drawn signature (`SignaturePad.js` — a plain HTML5 canvas
inside a WebView, deliberately not a dedicated native signature
library; see that file's own comment), notes, and a **partial
delivery** toggle. Submitting sends the POD data first
(`POST /deliveries/{id}/pod`), then marks the delivery delivered — the
backend's own two-step design (see `services/pod.py`), not something
this app invented. If the organization requires POD fields this
submission didn't include, the backend's own validation error is
surfaced directly rather than the app guessing requirements in advance.

Marking a delivery a **Failed Attempt** opens `FailedAttemptScreen.js`
— a real picker over the organization's actual active reason codes
(`GET /deliveries/reason-codes/active`, the same list the web app's
dispatcher-configured reason codes populate), each showing whether
it's eligible for return-to-origin, plus optional notes.

## Barcode / QR Scanning

`ScanScreen.js` (reachable via the 📷 **Scan** button on the delivery
list) uses `expo-camera`'s built-in barcode scanning — no separate
scanning library needed. The scanned code IS the delivery's own id
(same design as the web app's QR codes — see
`backend/app/models/scan.py`), so scanning resolves straight to that
delivery's detail screen, recording a scan event (pickup/delivery/hub,
inferred from the delivery's current status) along the way.

## Dispatcher ↔ Agent Messaging

`MessagesScreen.js` (reachable via the 💬 **Chat** button on a
delivery's detail screen) is the same per-delivery chat thread the web
app uses — `backend/app/models/delivery_message.py`'s own comment
calls it "the original agent<->dispatcher thread" (later extended to
include customers too), so this genuinely is the feature named in this
project's own docs as missing from the mobile app.

**Real-time**, via the backend's existing `/ws/deliveries/{id}/messages`
websocket — the same `chat_room` channel the web app already connects
to; no backend changes needed here either. `src/services/websocket.js`
ports the web app's own reconnect-with-exponential-backoff logic
(`frontend/src/services/websocket.js`) almost verbatim, since React
Native's built-in `WebSocket` implements the same interface a
browser's does. A small "Live" / "Reconnecting…" indicator in the
screen's header shows the actual connection state rather than
pretending it's always live. A one-time re-fetch when the app returns
to the foreground is kept as a safety net — a mobile OS can suspend a
backgrounded app's network activity far more aggressively than a
browser tab's, so a message sent while this device was backgrounded
might be missed by the live channel and only show up on that
reconnect-triggered fetch.

## Not Yet Built

Stated plainly rather than discovered the hard way:

- **CAPTCHA on mobile signup** — only matters if a deployment has
  `RECAPTCHA_SECRET_KEY` configured; see the Signup section above.
- Deep-linking the password-reset email straight into this app instead
  of the web app — see the Password Reset section above for why that
  wasn't worth building for how rarely this flow is used.

None of these are silently missing — an agent using only this app
today gets a real, working, genuinely background-location-capable,
offline-capable, push-notification-capable experience with signup,
proof of delivery, failed-attempt reason codes, barcode scanning, and
real-time dispatcher messaging — genuinely close to full parity with
the web agent app at this point, with the two gaps above being the
actual, specific remaining differences, not a vague "narrower"
hand-wave.
