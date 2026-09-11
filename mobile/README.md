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
| `GET /auth/me` | Restoring a session on app relaunch |
| `GET /deliveries/mine` | The agent's delivery list |
| `GET /deliveries/{id}` | Delivery detail |
| `PATCH /deliveries/{id}` | Advancing delivery status |
| `PUT /users/me/location` | Both the manual foreground case AND the background task's periodic pings |

No backend code was written or changed to support this app — see
`mobile/src/services/api.js`'s own docstring for why that's true by
design, not by coincidence: the location endpoint in particular was
already generic (any authenticated agent client calling it), it just
never had a client that could call it from the background before.

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

30 tests (Jest + jest-expo) covering the offline queue's actual logic —
`offlineStore.js`'s AsyncStorage-backed cache (per-user scoping, not
clobbering a pending edit with stale server data, pending-count
tracking) and `offlineSync.js`'s retry/backoff behavior and
foreground/connectivity-triggered sync, using real fake timers the
same way the web app's own `syncEngine.test.js` does — plus
`pushNotifications.js`'s permission/token/error-handling logic (8
tests; the one branch not covered — no physical device — is a single
early-return guard documented as untested in that test file's own
comment, a genuine Jest/Babel module-mocking limitation, not an
oversight). Screen
components (Login, DeliveryList, etc.) don't have tests yet — the
logic layer was prioritized since it's where an offline-sync bug would
actually cost real data.

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

## Not Yet Built

Stated plainly rather than discovered the hard way:

- **Signup / password reset** — login only. Create the agent account
  on the web app first, then log into this app with the same
  credentials.
- **Proof of delivery capture** (signature/photo), **partial
  delivery**, and **failed-attempt reason codes** — the web app
  supports all three when marking a delivery; this app's "Mark
  Delivered" is a simple one-tap status change with none of them.
- **Barcode/QR scanning** — the web app uses the browser's native
  `BarcodeDetector`; this app has no scanning at all yet
  (`expo-camera` + a barcode-scanning library would be the addition).
- **Dispatcher ↔ agent messaging** — exists on the web app, not here.

None of these are silently missing — an agent using only this app
today gets a real, working, genuinely background-location-capable,
offline-capable, push-notification-capable experience for the core
loop (see assigned deliveries, advance status even with no signal,
share live location, get notified of a new assignment), just a
narrower one than the full web agent app.
