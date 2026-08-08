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
} from "../services/api";
import StatusBadge from "./StatusBadge";

const STATUS_LABELS = {
  confirmed: "Order Confirmed",
  picked_up: "Picked Up",
  out_for_delivery: "Out for Delivery",
  delivered: "Delivered",
  failed_attempt: "Delivery Attempt Failed",
};

export default function CustomerDashboard() {
  const { customer, token, logout } = useCustomerAuth();
  const { theme, toggleTheme } = useTheme();
  const [deliveries, setDeliveries] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadDeliveries();
    loadNotifications();
    const intervalId = setInterval(loadNotifications, 10000);
    return () => clearInterval(intervalId);
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
        }}
      >
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, color: "var(--accent)" }}>
          Delivery Sync
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Hi, {customer.name}</span>
          <button className="btn" onClick={() => setShowNotifications(!showNotifications)}>
            🔔{unreadCount > 0 && ` (${unreadCount})`}
          </button>
          <button className="btn" onClick={toggleTheme}>{theme === "dark" ? "☀" : "☾"}</button>
          <button className="btn" onClick={logout}>Log out</button>
        </div>
      </div>

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
          />
        ))}
      </div>
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

function CustomerDeliveryCard({ delivery, token, isExpanded, onToggle }) {
  const [history, setHistory] = useState([]);
  const [feedback, setFeedback] = useState(null);
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedbackError, setFeedbackError] = useState(null);

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

      <button className="btn-info-outline" style={{ marginTop: "10px" }} onClick={onToggle}>
        {isExpanded ? "Hide Details" : "View Details"}
      </button>

      {isExpanded && (
        <div style={{ marginTop: "14px" }}>
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
