/**
 * Handles all HTTP calls to the FastAPI backend.
 *
 * Most endpoints now require a logged-in user, so every function here
 * takes a `token` (the JWT from AuthContext) and attaches it as a
 * 'Authorization: Bearer <token>' header. Components get this token from
 * `useAuth()` and pass it in.
 */

const API_BASE_URL = "http://127.0.0.1:8000";

function authHeaders(token) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

export async function createDeliveryOnServer(token, record) {
  const response = await fetch(`${API_BASE_URL}/deliveries/`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(record),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to create delivery on server");
  return data;
}

export async function updateDeliveryOnServer(token, id, update) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${id}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(update),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update delivery on server");
  return data;
}

export async function fetchAllDeliveriesFromServer(token) {
  const response = await fetch(`${API_BASE_URL}/deliveries/`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch deliveries from server");
  return data;
}

export async function fetchMyDeliveriesFromServer(token) {
  const response = await fetch(`${API_BASE_URL}/deliveries/mine`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch your deliveries from server");
  return data;
}

export async function deleteDeliveryOnServer(token, id) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${id}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to delete delivery on server");
  return data;
}

export async function fetchAgentsList(token) {
  const response = await fetch(`${API_BASE_URL}/users/agents`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch agents list");
  return data;
}

/**
 * Sends a batch of pending records to the server's /sync endpoint.
 * NOTE: this endpoint is intentionally left unauthenticated for now — see
 * docs/SECURITY_AND_ACCESS.md for why, and what a production version would
 * add here.
 */
export async function syncPendingDeliveries(pendingRecords) {
  const response = await fetch(`${API_BASE_URL}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ records: pendingRecords }),
  });
  if (!response.ok) throw new Error("Sync request failed");
  return response.json();
}
