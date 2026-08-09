import React, { useEffect, useState } from "react";
import { useCustomerAuth } from "../context/CustomerAuthContext";
import { useTheme } from "../context/ThemeContext";
import {
  fetchMyCustomerDeliveries,
  fetchMyCustomerDeliveryHistory,
  fetchMyCustomerDeliveryFeedback,
  fetchMyCustomerNotifications,
  markAllCustomerNotificationsRead,
  submitDeliveryFeedback,
  claimCustomerOrder,
  cancelCustomerDelivery,
  reorderCustomerDelivery,
  fetchMyCustomerAddresses,
  addCustomerAddress,
  deleteCustomerAddress,
  fetchVapidPublicKey,
  subscribeToPush,
} from "../services/api";
import StatusBadge from "./StatusBadge";
import LiveTrackingMap from "./LiveTrackingMap";
import Storefront from "./Storefront";
import "../styles/auth.css";

const STATUS_LABELS = {
  confirmed: "Order Confirmed",
  pending: "Placed (Awaiting Assignment)",
  picked_up: "Picked Up",
  out_for_delivery: "Out for Delivery",
  delivered: "Delivered",
  failed_attempt: "Delivery Attempt Failed",
  cancelled: "Cancelled",
};

const LIVE_TRACKABLE_STATUSES = ["picked_up", "out_for_delivery"];

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

export default function CustomerDashboard() {
  const { customer, token, logout } = useCustomerAuth();
  const { theme, toggleTheme } = useTheme();
  const [deliveries, setDeliveries] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [showAddresses, setShowAddresses] = useState(false);
  const [showShop, setShowShop] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState(null);
  const [pushStatus, setPushStatus] = useState("idle");

  useEffect(() => {
    loadDeliveries();
    loadNotifications();
    const intervalId = setInterval(loadNotifications, 10000);
    return () => clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (!("Notification" in window)) {
      setPushStatus("unsupported");
    } else if (Notification.permission === "granted") {
      setPushStatus("enabled");
    } else if (Notification.permission === "denied") {
      setPushStatus("denied");
    }
  }, []);

  async function loadDeliveries() {
    try {
      const data = await fetchMyCustomerDeliveries(token);
      setDeliveries(data);
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadNotifications() {
    try {
      const data = await fetchMyCustomerNotifications(token);
      setNotifications(data);
    } catch (err) {
      console.warn("Could not load notifications:", err.message);
    }
  }

  async function handleMarkAllRead() {
    await markAllCustomerNotificationsRead(token);
    await loadNotifications();
  }

  async function handleEnablePush() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      setPushStatus("unsupported");
      return;
    }
    setPushStatus("enabling");
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setPushStatus(permission === "denied" ? "denied" : "idle");
        return;
      }

      const registration = await navigator.serviceWorker.ready;
      const { public_key: vapidPublicKey } = await fetchVapidPublicKey(token);

      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
        });
      }

      await subscribeToPush(token, subscription.toJSON());
      setPushStatus("enabled");
    } catch (err) {
      console.warn("Push enable failed:", err.message);
      setPushStatus("idle");
      setError("Couldn't enable push notifications: " + err.message);
    }
  }

  const unreadCount = notifications.filter((n) => !n.is_read).length;

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "var(--bg-page)" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "16px 24px",
          backgroundColor: "var(--bg-surface)",
          borderBottom: "1px solid var(--border-color)",
          flexWrap: "wrap",
          gap: "8px",
        }}
      >
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, color: "var(--accent)" }}>
          Delivery Sync
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Hi, {customer.name}</span>
          {pushStatus !== "enabled" && pushStatus !== "unsupported" && (
            <button className="btn" onClick={handleEnablePush} disabled={pushStatus === "enabling"}>
              {pushStatus === "enabling" ? "Enabling..." : pushStatus === "denied" ? "Notifications Blocked" : "🔔 Enable Push"}
            </button>
          )}
          <button className="btn btn-primary" onClick={() => setShowShop(!showShop)}>
            🛍️ Shop
          </button>
          <button className="btn" onClick={() => setShowAddresses(!showAddresses)}>
            📍 Addresses
          </button>
          <button className="btn" onClick={() => setShowNotifications(!showNotifications)}>
            🔔{unreadCount > 0 && ` (${unreadCount})`}
          </button>
          <button className="btn" onClick={toggleTheme}>{theme === "dark" ? "☀" : "☾"}</button>
          <button className="btn" onClick={logout}>Log out</button>
        </div>
      </div>

      {showShop && (
        <div style={{ margin: "16px 24px" }}>
          <Storefront token={token} onOrderPlaced={() => { loadDeliveries(); setShowShop(false); }} />
        </div>
      )}

      {showAddresses && (
        <div style={{ margin: "16px 24px" }}>
          <AddressBook token={token} />
        </div>
      )}

      {showNotifications && (
        <div className="card" style={{ margin: "16px 24px", maxWidth: "420px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
            <strong style={{ fontSize: "13.5px" }}>Notifications</strong>
            {unreadCount > 0 && (
              <button className="btn" onClick={handleMarkAllRead} style={{ fontSize: "12px", padding: "4px 8px" }}>
                Mark all read
              </button>
            )}
          </div>
          {notifications.length === 0 && (
            <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>No notifications yet.</p>
          )}
          <div style={{ maxHeight: "260px", overflowY: "auto" }}>
            {notifications.map((n) => (
              <div
                key={n.id}
                style={{
                  padding: "8px 0",
                  borderBottom: "1px solid var(--border-color)",
                  opacity: n.is_read ? 0.6 : 1,
                }}
              >
                <div style={{ fontSize: "13px" }}>{n.message}</div>
                <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                  {new Date(n.created_at).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ padding: "24px", maxWidth: "700px", margin: "0 auto" }}>
        <h2 className="page-title">My Orders</h2>

        {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

        {deliveries.length === 0 && (
          <p style={{ color: "var(--text-secondary)" }}>
            No orders linked to your account yet. Orders placed under this
            email address will show up here automatically.
          </p>
        )}

        <ClaimOrderPanel token={token} onLinked={loadDeliveries} />

        {deliveries.map((delivery) => (
          <CustomerDeliveryCard
            key={delivery.id}
            delivery={delivery}
            token={token}
            isExpanded={expandedId === delivery.id}
            onToggle={() => setExpandedId(expandedId === delivery.id ? null : delivery.id)}
            onChanged={loadDeliveries}
          />
        ))}
      </div>
    </div>
  );
}

function AddressBook({ token }) {
  const [addresses, setAddresses] = useState([]);
  const [isAdding, setIsAdding] = useState(false);
  const [label, setLabel] = useState("");
  const [addressLine, setAddressLine] = useState("");
  const [city, setCity] = useState("");
  const [phone, setPhone] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      const data = await fetchMyCustomerAddresses(token);
      setAddresses(data);
    } catch (err) {
      console.warn("Could not load addresses:", err.message);
    }
  }

  async function handleAdd(e) {
    e.preventDefault();
    setError(null);
    try {
      await addCustomerAddress(token, {
        label: label.trim(),
        address_line: addressLine.trim(),
        city: city.trim() || null,
        phone: phone.trim() || null,
        is_default: isDefault,
      });
      setLabel("");
      setAddressLine("");
      setCity("");
      setPhone("");
      setIsDefault(false);
      setIsAdding(false);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDelete(id) {
    try {
      await deleteCustomerAddress(token, id);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="card" style={{ maxWidth: "500px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
        <strong style={{ fontSize: "13.5px" }}>Saved Addresses</strong>
        <button className="btn" style={{ fontSize: "12px", padding: "4px 8px" }} onClick={() => setIsAdding(!isAdding)}>
          {isAdding ? "Cancel" : "+ Add"}
        </button>
      </div>

      {addresses.length === 0 && !isAdding && (
        <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>No saved addresses yet.</p>
      )}

      {addresses.map((addr) => (
        <div
          key={addr.id}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            padding: "8px 0",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <div>
            <div style={{ fontSize: "13px", fontWeight: 600 }}>
              {addr.label} {addr.is_default && <span style={{ color: "var(--accent)", fontSize: "11px" }}>(Default)</span>}
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
              {addr.address_line}{addr.city && `, ${addr.city}`}
            </div>
            {addr.phone && <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{addr.phone}</div>}
          </div>
          <button className="btn" style={{ fontSize: "11px", padding: "3px 7px" }} onClick={() => handleDelete(addr.id)}>
            Delete
          </button>
        </div>
      ))}

      {isAdding && (
        <form onSubmit={handleAdd} style={{ marginTop: "12px" }}>
          <div className="auth-field">
            <label>Label (e.g. Home, Work)</label>
            <input className="input" type="text" value={label} onChange={(e) => setLabel(e.target.value)} required />
          </div>
          <div className="auth-field">
            <label>Address</label>
            <input className="input" type="text" value={addressLine} onChange={(e) => setAddressLine(e.target.value)} required />
          </div>
          <div className="auth-field">
            <label>City</label>
            <input className="input" type="text" value={city} onChange={(e) => setCity(e.target.value)} />
          </div>
          <div className="auth-field">
            <label>Phone</label>
            <input className="input" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>
          <label style={{ fontSize: "12.5px", display: "flex", alignItems: "center", gap: "6px", marginBottom: "10px" }}>
            <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
            Set as default
          </label>
          {error && <p style={{ color: "var(--danger)", fontSize: "12px" }}>{error}</p>}
          <button type="submit" className="btn btn-primary">Save Address</button>
        </form>
      )}
    </div>
  );
}

function ClaimOrderPanel({ token, onLinked }) {
  const [isOpen, setIsOpen] = useState(false);
  const [orderId, setOrderId] = useState("");
  const [phone, setPhone] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSuccess(false);
    setIsSubmitting(true);
    try {
      await claimCustomerOrder(token, orderId.trim(), phone.trim());
      setSuccess(true);
      setOrderId("");
      setPhone("");
      await onLinked();
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: "20px" }}>
      <div
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
        onClick={() => setIsOpen(!isOpen)}
      >
        <strong style={{ fontSize: "13.5px" }}>Don't see an order? Link it manually</strong>
        <span style={{ fontSize: "12px", color: "var(--accent)" }}>{isOpen ? "Hide" : "Link an order"}</span>
      </div>

      {isOpen && (
        <div style={{ marginTop: "14px" }}>
          <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "12px" }}>
            If an order was placed under a different email than the one
            you signed up with, enter its Order ID and the phone number
            on file for it to link it to your account.
          </p>
          <form onSubmit={handleSubmit}>
            <div className="auth-field">
              <label>Order ID</label>
              <input className="input" type="text" value={orderId} onChange={(e) => setOrderId(e.target.value)} required />
            </div>
            <div className="auth-field">
              <label>Phone number on the order</label>
              <input className="input" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} required />
            </div>
            {error && <p style={{ color: "var(--danger)", fontSize: "12px" }}>{error}</p>}
            {success && <p style={{ color: "var(--accent)", fontSize: "12px" }}>Order linked to your account.</p>}
            <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
              {isSubmitting ? "Linking..." : "Link order"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

function CustomerDeliveryCard({ delivery, token, isExpanded, onToggle, onChanged }) {
  const [history, setHistory] = useState([]);
  const [feedback, setFeedback] = useState(null);
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedbackError, setFeedbackError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isReordering, setIsReordering] = useState(false);

  useEffect(() => {
    if (isExpanded) loadDetails();
  }, [isExpanded]);

  async function loadDetails() {
    try {
      const [historyData, feedbackData] = await Promise.all([
        fetchMyCustomerDeliveryHistory(token, delivery.id),
        fetchMyCustomerDeliveryFeedback(token, delivery.id),
      ]);
      setHistory(historyData);
      setFeedback(feedbackData);
    } catch (err) {
      console.warn("Could not load order details:", err.message);
    }
  }

  async function handleSubmitFeedback(e) {
    e.preventDefault();
    if (rating === 0) {
      setFeedbackError("Please pick a star rating.");
      return;
    }
    setIsSubmitting(true);
    setFeedbackError(null);
    try {
      await submitDeliveryFeedback(delivery.id, rating, comment.trim());
      await loadDetails();
    } catch (err) {
      setFeedbackError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCancel() {
    if (!window.confirm(`Cancel order ${delivery.order_id}? This can't be undone.`)) return;
    setIsCancelling(true);
    setActionError(null);
    try {
      await cancelCustomerDelivery(token, delivery.id);
      await onChanged();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setIsCancelling(false);
    }
  }

  async function handleReorder() {
    setIsReordering(true);
    setActionError(null);
    try {
      await reorderCustomerDelivery(token, delivery.id);
      await onChanged();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setIsReordering(false);
    }
  }

  const canCancel = delivery.status === "picked_up" || delivery.status === "pending";
  const canReorder = ["delivered", "cancelled", "failed_attempt"].includes(delivery.status);
  const isLiveTrackable = LIVE_TRACKABLE_STATUSES.includes(delivery.status);

  return (
    <div className="delivery-card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="delivery-card-order-id">{delivery.order_id}</span>
        <StatusBadge status={delivery.status} />
      </div>
      <div style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginTop: "6px" }}>
        Last updated: {new Date(delivery.updated_at).toLocaleString()}
        {delivery.expected_by && ` · Expected by ${new Date(delivery.expected_by).toLocaleString()}`}
      </div>

      <div style={{ display: "flex", gap: "8px", marginTop: "10px", flexWrap: "wrap" }}>
        <button className="btn-info-outline" onClick={onToggle}>
          {isExpanded ? "Hide Details" : "View Details"}
        </button>
        {canCancel && (
          <button className="btn" style={{ color: "var(--danger)" }} onClick={handleCancel} disabled={isCancelling}>
            {isCancelling ? "Cancelling..." : "Cancel Order"}
          </button>
        )}
        {canReorder && (
          <button className="btn" onClick={handleReorder} disabled={isReordering}>
            {isReordering ? "Placing..." : "Reorder"}
          </button>
        )}
      </div>
      {actionError && <p style={{ color: "var(--danger)", fontSize: "12px", marginTop: "6px" }}>{actionError}</p>}

      {isExpanded && (
        <div style={{ marginTop: "14px" }}>
          {isLiveTrackable && (
            <div style={{ marginBottom: "14px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px" }}>
                Live Location
              </div>
              <LiveTrackingMap token={token} deliveryId={delivery.id} />
            </div>
          )}

          {delivery.proof_of_delivery && (
            <div style={{ marginBottom: "14px" }}>
              <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "6px" }}>
                Proof of Delivery
              </div>
              <img
                src={delivery.proof_of_delivery}
                alt="Proof of delivery"
                style={{ maxWidth: "100%", maxHeight: "160px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-color)" }}
              />
            </div>
          )}

          <div style={{ fontSize: "12.5px", fontWeight: 600, marginBottom: "8px" }}>Timeline</div>
          {history.map((entry, i) => (
            <div key={i} style={{ borderLeft: "3px solid var(--accent)", paddingLeft: "10px", marginBottom: "8px" }}>
              <div style={{ fontSize: "12.5px", fontWeight: 600 }}>
                {entry.old_status
                  ? `${STATUS_LABELS[entry.old_status] || entry.old_status} → ${STATUS_LABELS[entry.new_status] || entry.new_status}`
                  : "Order Confirmed"}
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-secondary)" }}>
                {new Date(entry.changed_at).toLocaleString()}
              </div>
            </div>
          ))}

          {delivery.status === "delivered" && (
            <div style={{ marginTop: "14px" }}>
              <div style={{ fontSize: "12.5px", fontWeight: 600, marginBottom: "8px" }}>
                How was your delivery?
              </div>
              {feedback ? (
                <div>
                  <div style={{ fontSize: "18px", letterSpacing: "2px" }}>
                    {"★".repeat(feedback.rating)}
                    <span style={{ color: "var(--text-muted)" }}>{"★".repeat(5 - feedback.rating)}</span>
                  </div>
                  {feedback.comment && (
                    <p style={{ fontSize: "12.5px", color: "var(--text-secondary)" }}>"{feedback.comment}"</p>
                  )}
                </div>
              ) : (
                <form onSubmit={handleSubmitFeedback}>
                  <div style={{ fontSize: "22px", marginBottom: "8px" }}>
                    {[1, 2, 3, 4, 5].map((star) => (
                      <span
                        key={star}
                        onClick={() => setRating(star)}
                        style={{ cursor: "pointer", color: star <= rating ? "var(--accent)" : "var(--text-muted)" }}
                      >
                        ★
                      </span>
                    ))}
                  </div>
                  <textarea
                    className="input"
                    placeholder="Optional comment..."
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    style={{ width: "100%", minHeight: "50px", marginBottom: "8px" }}
                  />
                  {feedbackError && <p style={{ color: "var(--danger)", fontSize: "12px" }}>{feedbackError}</p>}
                  <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
                    {isSubmitting ? "Submitting..." : "Submit Feedback"}
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
