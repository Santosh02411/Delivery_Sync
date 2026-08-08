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

export async function fetchDeliveryHistory(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}/history`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch delivery history");
  return data;
}

export async function fetchDeliveryMessages(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}/messages`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch messages");
  return data;
}

export async function sendDeliveryMessage(token, deliveryId, message) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}/messages`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ message }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to send message");
  return data;
}

export async function bulkImportDeliveries(token, rows) {
  const response = await fetch(`${API_BASE_URL}/deliveries/bulk-import`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ rows }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Bulk import failed");
  return data;
}

/**
 * Downloads the CSV export and triggers a browser "Save File" prompt.
 *
 * This can't just be a plain <a href="..."> link, because the export
 * endpoint requires an Authorization header — a link click can't attach
 * custom headers. Instead: fetch it manually (with the header), turn the
 * response into a Blob, and programmatically click a temporary <a> tag
 * pointing at an object URL for that blob. This is the standard pattern
 * for downloading an authenticated file from the browser.
 */
export async function exportDeliveriesCSV(token, dateFrom, dateTo) {
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);

  const response = await fetch(`${API_BASE_URL}/deliveries/export?${params.toString()}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Export failed");
  }

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);

  // Extract the filename the server suggested, falling back to a generic one
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename=([^;]+)/);
  const filename = match ? match[1].trim() : "deliveries_export.csv";

  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
}

// ---------- Admin ----------

export async function fetchOrganizationUsers(token) {
  const response = await fetch(`${API_BASE_URL}/admin/users`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch organization users");
  return data;
}

export async function fetchOrganizationInfo(token) {
  const response = await fetch(`${API_BASE_URL}/admin/organization`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch organization info");
  return data;
}

export async function fetchPublicTracking(deliveryId) {
  const response = await fetch(`${API_BASE_URL}/track/${deliveryId}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Tracking info not found");
  return data;
}

export async function submitDeliveryFeedback(deliveryId, rating, comment) {
  const response = await fetch(`${API_BASE_URL}/track/${deliveryId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating, comment: comment || null }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to submit feedback");
  return data;
}

function customerAuthHeaders(token) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

export async function fetchMyCustomerDeliveries(token) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch your orders");
  return data;
}

export async function fetchMyCustomerDeliveryHistory(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries/${deliveryId}/history`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch order history");
  return data;
}

export async function fetchMyCustomerDeliveryFeedback(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries/${deliveryId}/feedback`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch feedback");
  return data;
}

export async function fetchMyCustomerNotifications(token) {
  const response = await fetch(`${API_BASE_URL}/customer/notifications`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch notifications");
  return data;
}

export async function claimCustomerOrder(token, orderId, phone) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries/claim`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({ order_id: orderId, phone }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to link that order");
  return data;
}

export async function markAllCustomerNotificationsRead(token) {
  const response = await fetch(`${API_BASE_URL}/customer/notifications/mark-all-read`, {
    method: "POST",
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to mark notifications read");
  return data;
}

export async function deactivateUser(token, userId) {
  const response = await fetch(`${API_BASE_URL}/admin/users/${userId}/deactivate`, {
    method: "PATCH",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to deactivate user");
  return data;
}

export async function activateUser(token, userId) {
  const response = await fetch(`${API_BASE_URL}/admin/users/${userId}/activate`, {
    method: "PATCH",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to activate user");
  return data;
}

export async function resetUserPassword(token, userId, newPassword) {
  const response = await fetch(`${API_BASE_URL}/admin/users/${userId}/reset-password`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ new_password: newPassword }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to reset password");
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
