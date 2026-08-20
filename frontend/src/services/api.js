/**
 * Handles all HTTP calls to the FastAPI backend.
 *
 * Most endpoints now require a logged-in user, so every function here
 * takes a `token` (the JWT from AuthContext) and attaches it as a
 * 'Authorization: Bearer <token>' header. Components get this token from
 * `useAuth()` and pass it in.
 */

// Defaults to the local FastAPI dev server so `npm run dev` works with
// zero config, same as always. VITE_API_BASE_URL lets a real build
// (Docker, or any other deployment) point at a different backend URL —
// Vite only inlines `import.meta.env.VITE_*` vars at BUILD time, not
// runtime, so this has to be set when the frontend is built (a
// `--build-arg` in Docker, or a `.env` file Vite reads), not afterwards.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

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

export async function fetchMyCustomerDeliveries(token, { limit, offset } = {}) {
  const params = new URLSearchParams();
  if (limit) params.set("limit", limit);
  if (offset) params.set("offset", offset);
  const response = await fetch(`${API_BASE_URL}/customer/deliveries?${params.toString()}`, {
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

export async function fetchMyCustomerNotifications(token, { limit, offset } = {}) {
  const params = new URLSearchParams();
  if (limit) params.set("limit", limit);
  if (offset) params.set("offset", offset);
  const response = await fetch(`${API_BASE_URL}/customer/notifications?${params.toString()}`, {
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

export async function cancelCustomerDelivery(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries/${deliveryId}/cancel`, {
    method: "POST",
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to cancel order");
  return data;
}

export async function reorderCustomerDelivery(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries/${deliveryId}/reorder`, {
    method: "POST",
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to reorder");
  return data;
}

export async function fetchCustomerDeliveryAgentLocation(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries/${deliveryId}/agent-location`, {
    headers: customerAuthHeaders(token),
  });
  if (!response.ok) return null; // 404 just means agent hasn't shared location yet — not an error state
  return response.json();
}

export async function fetchMyCustomerAddresses(token) {
  const response = await fetch(`${API_BASE_URL}/customer/addresses`, {
    headers: customerAuthHeaders(token),
  });
  return response.json();
}

export async function addCustomerAddress(token, address) {
  const response = await fetch(`${API_BASE_URL}/customer/addresses`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify(address),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to save address");
  return data;
}

export async function deleteCustomerAddress(token, addressId) {
  const response = await fetch(`${API_BASE_URL}/customer/addresses/${addressId}`, {
    method: "DELETE",
    headers: customerAuthHeaders(token),
  });
  if (!response.ok) throw new Error("Failed to delete address");
  return response.json();
}

export async function fetchVapidPublicKey(token) {
  const response = await fetch(`${API_BASE_URL}/customer/push/vapid-public-key`, {
    headers: customerAuthHeaders(token),
  });
  return response.json();
}

export async function subscribeToPush(token, subscription) {
  const response = await fetch(`${API_BASE_URL}/customer/push/subscribe`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify(subscription),
  });
  if (!response.ok) throw new Error("Failed to subscribe to push notifications");
  return response.json();
}

export async function updateMyAgentLocation(token, latitude, longitude) {
  const response = await fetch(`${API_BASE_URL}/users/me/location`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ latitude, longitude }),
  });
  if (!response.ok) throw new Error("Failed to update location");
  return response.json();
}

// ---------- Storefront / Cart / Checkout ----------

export async function fetchPublicStores() {
  const response = await fetch(`${API_BASE_URL}/stores/`);
  return response.json();
}

export async function fetchStoreProducts(orgId) {
  const response = await fetch(`${API_BASE_URL}/stores/${orgId}/products`);
  return response.json();
}

export async function fetchMyCart(token) {
  const response = await fetch(`${API_BASE_URL}/customer/cart/`, {
    headers: customerAuthHeaders(token),
  });
  return response.json();
}

export async function addToCart(token, productId, quantity = 1) {
  const response = await fetch(`${API_BASE_URL}/customer/cart/`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({ product_id: productId, quantity }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to add to cart");
  return data;
}

export async function updateCartItem(token, itemId, quantity) {
  const response = await fetch(`${API_BASE_URL}/customer/cart/${itemId}`, {
    method: "PATCH",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({ quantity }),
  });
  return response.json();
}

export async function removeCartItem(token, itemId) {
  const response = await fetch(`${API_BASE_URL}/customer/cart/${itemId}`, {
    method: "DELETE",
    headers: customerAuthHeaders(token),
  });
  return response.json();
}

export async function clearCart(token) {
  const response = await fetch(`${API_BASE_URL}/customer/cart/`, {
    method: "DELETE",
    headers: customerAuthHeaders(token),
  });
  return response.json();
}

export async function checkoutCart(token, addressLine, city, phone, couponCode, slotStart, paymentMethod = "online") {
  const response = await fetch(`${API_BASE_URL}/customer/checkout`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({
      address_line: addressLine,
      city,
      phone,
      coupon_code: couponCode || null,
      slot_start: slotStart || null,
      payment_method: paymentMethod,
    }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Checkout failed");
  return data;
}

export async function fetchDeliverySlots(orgId, dateStr) {
  const response = await fetch(`${API_BASE_URL}/stores/${orgId}/delivery-slots?date=${dateStr}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load delivery slots");
  return data;
}

export async function validateCoupon(token, code) {
  const response = await fetch(`${API_BASE_URL}/customer/checkout/validate-coupon`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({ code }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Invalid coupon");
  return data;
}

export async function verifyPayment(token, payload) {
  const response = await fetch(`${API_BASE_URL}/customer/checkout/verify`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Payment verification failed");
  return data;
}

export async function fetchMyOrders(token, { limit, offset, deliveryId } = {}) {
  const params = new URLSearchParams();
  if (limit) params.set("limit", limit);
  if (offset) params.set("offset", offset);
  if (deliveryId) params.set("delivery_id", deliveryId);
  const response = await fetch(`${API_BASE_URL}/customer/orders?${params.toString()}`, {
    headers: customerAuthHeaders(token),
  });
  return response.json();
}

// ---------- Product reviews ----------

export async function fetchProductReviews(productId) {
  const response = await fetch(`${API_BASE_URL}/stores/products/${productId}/reviews`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch reviews");
  return data;
}

export async function fetchReviewableItems(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/customer/deliveries/${deliveryId}/reviewable-items`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to fetch reviewable items");
  return data;
}

export async function submitProductReview(token, productId, orderId, rating, comment) {
  const response = await fetch(`${API_BASE_URL}/customer/products/${productId}/reviews`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({ order_id: orderId, rating, comment: comment || null }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to submit review");
  return data;
}

// ---------- Dispatcher: product catalog + unassigned orders ----------

export async function fetchMyProducts(token) {
  const response = await fetch(`${API_BASE_URL}/admin/products/`, {
    headers: authHeaders(token),
  });
  return response.json();
}

export async function uploadProductImage(token, file) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/admin/products/upload-image`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }, // no Content-Type — the browser sets the multipart boundary itself
    body: formData,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to upload image");
  return data;
}

export async function createProduct(token, product) {
  const response = await fetch(`${API_BASE_URL}/admin/products/`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(product),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to create product");
  return data;
}

export async function updateProduct(token, productId, updates) {
  const response = await fetch(`${API_BASE_URL}/admin/products/${productId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(updates),
  });
  return response.json();
}

export async function deleteProduct(token, productId) {
  const response = await fetch(`${API_BASE_URL}/admin/products/${productId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  return response.json();
}

export async function fetchMyOrganization(token) {
  const response = await fetch(`${API_BASE_URL}/admin/organization`, {
    headers: authHeaders(token),
  });
  return response.json();
}

export async function fetchAnalytics(token, days = 30) {
  const response = await fetch(`${API_BASE_URL}/admin/analytics/?days=${days}`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load analytics");
  return data;
}

// ---------- Staff push notifications ----------

export async function fetchStaffVapidPublicKey(token) {
  const response = await fetch(`${API_BASE_URL}/users/me/push/vapid-public-key`, {
    headers: authHeaders(token),
  });
  return response.json();
}

export async function subscribeStaffToPush(token, subscription) {
  const response = await fetch(`${API_BASE_URL}/users/me/push/subscribe`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(subscription),
  });
  if (!response.ok) throw new Error("Failed to subscribe to push notifications");
  return response.json();
}
export async function setStoreVisibility(token, isPublic) {
  const response = await fetch(`${API_BASE_URL}/admin/store/visibility`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ is_public_store: isPublic }),
  });
  return response.json();
}

export async function setStorePricing(token, deliveryFee, taxRatePercent) {
  const response = await fetch(`${API_BASE_URL}/admin/store/pricing`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ delivery_fee: deliveryFee, tax_rate_percent: taxRatePercent }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update pricing");
  return data;
}

export async function setStoreSlotSettings(token, settings) {
  const response = await fetch(`${API_BASE_URL}/admin/store/slot-settings`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(settings),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update slot settings");
  return data;
}

// ---------- Coupons ----------

export async function fetchMyCoupons(token) {
  const response = await fetch(`${API_BASE_URL}/admin/coupons/`, {
    headers: authHeaders(token),
  });
  return response.json();
}

export async function createCoupon(token, coupon) {
  const response = await fetch(`${API_BASE_URL}/admin/coupons/`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(coupon),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to create coupon");
  return data;
}

export async function updateCoupon(token, couponId, updates) {
  const response = await fetch(`${API_BASE_URL}/admin/coupons/${couponId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(updates),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update coupon");
  return data;
}

export async function deleteCoupon(token, couponId) {
  const response = await fetch(`${API_BASE_URL}/admin/coupons/${couponId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  return response.json();
}

export async function fetchUnassignedDeliveries(token) {
  const response = await fetch(`${API_BASE_URL}/deliveries/unassigned`, {
    headers: authHeaders(token),
  });
  return response.json();
}

export async function assignAgentToDelivery(token, deliveryId, agentId) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}/assign-agent`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ agent_id: agentId }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to assign agent");
  return data;
}

export async function fetchSuggestedAgents(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}/suggested-agents`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load agent suggestions");
  return data;
}

export async function optimizeRouteOnServer(token, deliveryIds, startLatitude, startLongitude) {
  const response = await fetch(`${API_BASE_URL}/deliveries/optimize-route`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({
      delivery_ids: deliveryIds,
      start_latitude: startLatitude ?? null,
      start_longitude: startLongitude ?? null,
    }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to optimize route");
  return data; // { ordered_delivery_ids, used_real_routing }
}

export async function autoAssignDelivery(token, deliveryId) {
  const response = await fetch(`${API_BASE_URL}/deliveries/${deliveryId}/auto-assign`, {
    method: "POST",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to auto-assign");
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

export async function deleteCustomerNotification(token, notificationId) {
  const response = await fetch(`${API_BASE_URL}/customer/notifications/${notificationId}`, {
    method: "DELETE",
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to delete notification");
  return data;
}

export async function clearCustomerNotifications(token, onlyRead = true) {
  const response = await fetch(`${API_BASE_URL}/customer/notifications?only_read=${onlyRead}`, {
    method: "DELETE",
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to clear notifications");
  return data;
}

export async function fetchMyCustomerProfile(token) {
  const response = await fetch(`${API_BASE_URL}/customer/me`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load your profile");
  return data;
}

export async function updateMyCustomerProfile(token, updates) {
  const response = await fetch(`${API_BASE_URL}/customer/me`, {
    method: "PATCH",
    headers: customerAuthHeaders(token),
    body: JSON.stringify(updates),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update your profile");
  return data;
}

export async function changeMyCustomerPassword(token, currentPassword, newPassword) {
  const response = await fetch(`${API_BASE_URL}/customer/me/change-password`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to change your password");
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

// ---------- Two-factor authentication (staff) ----------

export async function fetchTwoFactorStatus(token) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/status`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load two-factor status");
  return data; // { totp_enabled }
}

export async function setupTwoFactor(token) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/setup`, {
    method: "POST",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to start two-factor setup");
  return data; // { secret, otpauth_uri }
}

export async function enableTwoFactor(token, code) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/enable`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ code }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to enable two-factor authentication");
  return data;
}

export async function disableTwoFactor(token, password) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/disable`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ password }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to disable two-factor authentication");
  return data;
}

// ---------- Admin audit log ----------

export async function fetchAuditLog(token, { dateFrom, dateTo, changedByUserId, orderId, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (changedByUserId) params.set("changed_by_user_id", changedByUserId);
  if (orderId) params.set("order_id", orderId);
  if (limit) params.set("limit", limit);
  if (offset) params.set("offset", offset);

  const response = await fetch(`${API_BASE_URL}/admin/audit-log?${params.toString()}`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load audit log");
  return data;
}

// ---------- General admin action log (users/products/coupons/store settings) ----------

export async function fetchActionLog(token, { action, entityType, actorUserId, limit, offset } = {}) {
  const params = new URLSearchParams();
  if (action) params.set("action", action);
  if (entityType) params.set("entity_type", entityType);
  if (actorUserId) params.set("actor_user_id", actorUserId);
  if (limit) params.set("limit", limit);
  if (offset) params.set("offset", offset);

  const response = await fetch(`${API_BASE_URL}/admin/action-log?${params.toString()}`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load action log");
  return data;
}

// ---------- Delivery zones/territories ----------

export async function fetchZones(token) {
  const response = await fetch(`${API_BASE_URL}/admin/zones/`, { headers: authHeaders(token) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load zones");
  return data;
}

export async function createZone(token, zone) {
  const response = await fetch(`${API_BASE_URL}/admin/zones/`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(zone),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to create zone");
  return data;
}

export async function updateZone(token, zoneId, updates) {
  const response = await fetch(`${API_BASE_URL}/admin/zones/${zoneId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(updates),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update zone");
  return data;
}

export async function deleteZone(token, zoneId) {
  const response = await fetch(`${API_BASE_URL}/admin/zones/${zoneId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to delete zone");
  return data;
}

export async function assignAgentToZone(token, zoneId, agentId) {
  const response = await fetch(`${API_BASE_URL}/admin/zones/${zoneId}/agents/${agentId}`, {
    method: "POST",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to assign agent to zone");
  return data;
}

export async function unassignAgentFromZone(token, zoneId, agentId) {
  const response = await fetch(`${API_BASE_URL}/admin/zones/${zoneId}/agents/${agentId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to remove agent from zone");
  return data;
}

// ---------- Returns / exchanges ----------

export async function createReturnRequest(token, deliveryId, requestType, reason) {
  const response = await fetch(`${API_BASE_URL}/customer/return-requests/`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify({ delivery_id: deliveryId, request_type: requestType, reason }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to submit request");
  return data;
}

export async function fetchMyReturnRequests(token) {
  const response = await fetch(`${API_BASE_URL}/customer/return-requests/`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load return requests");
  return data;
}

export async function fetchReturnRequests(token, statusFilter) {
  const params = statusFilter ? `?status=${statusFilter}` : "";
  const response = await fetch(`${API_BASE_URL}/admin/return-requests/${params}`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load return requests");
  return data;
}

export async function approveReturnRequest(token, requestId, resolutionNote) {
  const response = await fetch(`${API_BASE_URL}/admin/return-requests/${requestId}/approve`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ resolution_note: resolutionNote || null }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to approve request");
  return data;
}

export async function rejectReturnRequest(token, requestId, resolutionNote) {
  const response = await fetch(`${API_BASE_URL}/admin/return-requests/${requestId}/reject`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ resolution_note: resolutionNote || null }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to reject request");
  return data;
}

// ---------- Customer data privacy (GDPR export / delete) ----------

export async function exportCustomerData(token) {
  const response = await fetch(`${API_BASE_URL}/customer/data-export`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.detail || "Failed to export your data");
  }
  return response.blob();
}

export async function deleteCustomerAccount(token, password) {
  const response = await fetch(`${API_BASE_URL}/customer/account`, {
    method: "DELETE",
    headers: authHeaders(token),
    body: JSON.stringify({ password }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to delete your account");
  return data;
}

// ---------- Agent coverage area (real GPS + reverse geocoding) ----------

export async function detectMyArea(token, latitude, longitude) {
  const response = await fetch(`${API_BASE_URL}/users/me/area/detect`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ latitude, longitude }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to detect your area");
  return data; // { area_name, area_latitude, area_longitude }
}

export async function clearMyArea(token) {
  const response = await fetch(`${API_BASE_URL}/users/me/area`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to clear your area");
  return data;
}

export async function setMyArea(token, areaName) {
  const response = await fetch(`${API_BASE_URL}/users/me/area/set`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ area_name: areaName }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to set your area");
  return data;
}

export async function fetchAreaSuggestions(token) {
  const response = await fetch(`${API_BASE_URL}/users/me/area/suggestions`, {
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load area suggestions");
  return data; // string[]
}

// ---------- Two-factor authentication: email-code method ----------

export async function setupEmailTwoFactor(token) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/setup-email`, {
    method: "POST",
    headers: authHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to send a confirmation code");
  return data; // { sent, masked_email }
}

export async function enableEmailTwoFactor(token, code) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/enable-email`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ code }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to enable two-factor authentication");
  return data;
}

export async function resendTwoFactorLoginCode(challengeToken) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/resend-code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ challenge_token: challengeToken, code: "" }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to resend the code");
  return data; // { sent, masked_email }
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

// ---------- Marketplace search (stores) ----------

export async function fetchPublicStoresFiltered(search, category) {
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (category) params.set("category", category);
  const qs = params.toString();
  const response = await fetch(`${API_BASE_URL}/stores/${qs ? `?${qs}` : ""}`);
  return response.json();
}

export async function fetchStoreCategories() {
  const response = await fetch(`${API_BASE_URL}/stores/categories`);
  return response.json();
}

export async function setStoreProfile(token, category, description) {
  const response = await fetch(`${API_BASE_URL}/admin/store/profile`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ category: category || null, description: description || null }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update store profile");
  return data;
}

// ---------- Recurring / subscription orders ----------

export async function fetchMySubscriptions(token) {
  const response = await fetch(`${API_BASE_URL}/customer/subscriptions/`, {
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to load subscriptions");
  return data;
}

export async function createSubscription(token, payload) {
  const response = await fetch(`${API_BASE_URL}/customer/subscriptions/`, {
    method: "POST",
    headers: customerAuthHeaders(token),
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to create subscription");
  return data;
}

export async function updateSubscription(token, subscriptionId, payload) {
  const response = await fetch(`${API_BASE_URL}/customer/subscriptions/${subscriptionId}`, {
    method: "PATCH",
    headers: customerAuthHeaders(token),
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to update subscription");
  return data;
}

async function _subscriptionAction(token, subscriptionId, action) {
  const response = await fetch(`${API_BASE_URL}/customer/subscriptions/${subscriptionId}/${action}`, {
    method: "POST",
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `Failed to ${action} subscription`);
  return data;
}

export const pauseSubscription = (token, id) => _subscriptionAction(token, id, "pause");
export const resumeSubscription = (token, id) => _subscriptionAction(token, id, "resume");
export const cancelSubscription = (token, id) => _subscriptionAction(token, id, "cancel");
export const runSubscriptionNow = (token, id) => _subscriptionAction(token, id, "run-now");

export async function initiateSubscriptionOrderPayment(token, orderId) {
  const response = await fetch(`${API_BASE_URL}/customer/subscriptions/orders/${orderId}/initiate-payment`, {
    method: "POST",
    headers: customerAuthHeaders(token),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Failed to start payment");
  return data;
}
