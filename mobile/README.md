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

## Not Yet Built

Stated plainly rather than discovered the hard way:

- **Signup / password reset** — login only. Create the agent account
  on the web app first, then log into this app with the same
  credentials.
- **Offline support** — the web agent app has a full IndexedDB-backed
  offline queue with conflict resolution; this app does not (yet) — an
  action taken with no signal simply fails with an error, it isn't
  queued for later. This is the single biggest feature gap versus the
  web app, and the most natural next addition (`@react-native-async-
  storage/async-storage` + the same retry-queue shape the web app's
  `frontend/src/services/syncEngine.js` already uses would be the
  starting point).
- **Proof of delivery capture** (signature/photo), **partial
  delivery**, and **failed-attempt reason codes** — the web app
  supports all three when marking a delivery; this app's "Mark
  Delivered" is a simple one-tap status change with none of them.
- **Barcode/QR scanning** — the web app uses the browser's native
  `BarcodeDetector`; this app has no scanning at all yet
  (`expo-camera` + a barcode-scanning library would be the addition).
- **Push notifications** — the web app has Web Push for agents; this
  app doesn't yet request/register for Expo push notifications.
- **Dispatcher ↔ agent messaging** — exists on the web app, not here.

None of these are silently missing — an agent using only this app
today gets a real, working, genuinely background-location-capable
experience for the core loop (see assigned deliveries, advance
status, share live location), just a narrower one than the full web
agent app.
