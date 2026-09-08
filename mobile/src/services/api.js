/**
 * Talks to the exact same backend as the web app (backend/main.py) —
 * no mobile-specific API surface exists or is needed. Every endpoint
 * used here (POST /auth/login, GET /deliveries/mine, PATCH
 * /deliveries/{id}, PUT /users/me/location) already exists, is already
 * tested (see backend/tests/), and is already used by the web
 * frontend — this file is a second, independent CLIENT of that same
 * API, not a new backend surface.
 *
 * The one thing genuinely different from the web app: PUT
 * /users/me/location gets called from a BACKGROUND task (see
 * ../locationTask.js) that keeps running while this app is
 * backgrounded or the phone is locked — something no browser tab can
 * do (see that file's own docstring for exactly why this needs a
 * native app at all).
 */

import * as SecureStore from "expo-secure-store";

// Change this to your deployed backend's URL for a real device build —
// 10.0.2.2 is the special alias the Android emulator uses to reach
// "localhost" on the machine running the emulator; a physical device
// needs your machine's real LAN IP instead (e.g. http://192.168.1.42:8000),
// since "localhost" on a phone means the phone itself.
export const API_BASE_URL = process.env.EXPO_PUBLIC_API_BASE_URL || "http://10.0.2.2:8000";

const TOKEN_KEY = "delivery_sync_access_token";
const REFRESH_TOKEN_KEY = "delivery_sync_refresh_token";

export async function saveTokens(accessToken, refreshToken) {
  await SecureStore.setItemAsync(TOKEN_KEY, accessToken);
  if (refreshToken) {
    await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, refreshToken);
  }
}

export async function getAccessToken() {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function getRefreshToken() {
  return SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
}

export async function clearTokens() {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
  await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
}

async function authHeaders() {
  const token = await getAccessToken();
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

/**
 * Staff login — identical request/response shape to the web app's
 * loginRequest() in frontend/src/services/authApi.js. Only agents are
 * a realistic audience for this app (dispatchers/admins need the full
 * console, not a phone screen), but the backend doesn't distinguish —
 * a dispatcher COULD log in here too, they'd just see an empty
 * delivery list since none would be assigned to them.
 */
export async function login(username, password) {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Login failed.");
  }
  return data; // { access_token, refresh_token, user } OR { requires_2fa: true, challenge_token }
}

export async function verifyTwoFactorLogin(challengeToken, code) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/verify-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ challenge_token: challengeToken, code }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Verification failed.");
  }
  return data;
}

export async function fetchMyDeliveries() {
  const response = await fetch(`${API_BASE_URL}/deliveries/mine`, {
    headers: await authHeaders(),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load deliveries.");
  return data;
}

export async function getDelivery(deliveryId) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}`, {
    headers: await authHeaders(),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load delivery.");
  return data;
}

/**
 * Same PATCH /deliveries/{id} the web app's offline sync engine
 * eventually calls — `updated_at` is required by the backend (see
 * DeliveryRecordUpdate in backend/app/models/delivery.py) as the
 * client-supplied timestamp its conflict-resolution logic compares
 * against, exactly like the web app's own sync engine already sends.
 */
export async function updateDeliveryStatus(deliveryId, status, extra = {}) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}`, {
    method: "PATCH",
    headers: await authHeaders(),
    body: JSON.stringify({
      status,
      updated_at: new Date().toISOString(),
      ...extra,
    }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update delivery.");
  return data;
}

/**
 * The one call this app makes that the web app effectively can't make
 * reliably in the background — see ../locationTask.js.
 */
export async function pushMyLocation(latitude, longitude) {
  const response = await fetch(`${API_BASE_URL}/users/me/location`, {
    method: "PUT",
    headers: await authHeaders(),
    body: JSON.stringify({ latitude, longitude }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Failed to push location.");
  }
  return response.json();
}

export async function fetchMyProfile() {
  const response = await fetch(`${API_BASE_URL}/auth/me`, {
    headers: await authHeaders(),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load profile.");
  return data;
}
