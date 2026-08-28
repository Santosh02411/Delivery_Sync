import React, { useEffect, useState } from "react";
import { fetchDeliveryHistory, fetchDeliveryAttempts } from "../services/api";
import { useAuth } from "../context/AuthContext";
import StatusBadge from "./StatusBadge";
import DeliveryMessages from "./DeliveryMessages";

const STATUS_LABELS = {
  picked_up: "Picked Up",
  out_for_delivery: "Out for Delivery",
  delivered: "Delivered",
  failed_attempt: "Failed Attempt",
};

const PRIORITY_LABELS = { low: "Low", normal: "Normal", high: "High", urgent: "Urgent" };

const ATTEMPT_OUTCOME_LABELS = {
  delivered: "Delivered",
  partial_delivery: "Partially Delivered",
  failed_attempt: "Failed Attempt",
};

/**
 * Modal showing full details of a single delivery record, PLUS its full
 * status-change history (audit log) fetched from the server: who changed
 * what, from what status to what, and when.
 *
 * History is only fetchable for records that have been synced to the
 * server (it needs a real server-side ID) — for a record still only
 * saved locally ("Saved locally" badge), history isn't available yet,
 * which this modal explains rather than showing a confusing empty state.
 */
export default function DeliveryDetailModal({ delivery, agentName, onClose }) {
  const { token } = useAuth();
  const [history, setHistory] = useState([]);
  const [historyError, setHistoryError] = useState(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [showQrCode, setShowQrCode] = useState(false);
  const [attempts, setAttempts] = useState([]);
  const [attemptsError, setAttemptsError] = useState(null);
  const [isLoadingAttempts, setIsLoadingAttempts] = useState(false);

  useEffect(() => {
    if (!delivery) return;
    loadHistory();
    loadAttempts();
  }, [delivery]);

  async function loadHistory() {
    setIsLoadingHistory(true);
    setHistoryError(null);
    try {
      const records = await fetchDeliveryHistory(token, delivery.id);
      setHistory(records);
    } catch (err) {
      setHistoryError(err.message);
    } finally {
      setIsLoadingHistory(false);
    }
  }

  async function loadAttempts() {
    if (delivery.sync_status === "pending") return; // no server ID yet
    setIsLoadingAttempts(true);
    setAttemptsError(null);
    try {
      const records = await fetchDeliveryAttempts(token, delivery.id);
      setAttempts(records);
    } catch (err) {
      setAttemptsError(err.message);
    } finally {
      setIsLoadingAttempts(false);
    }
  }

  if (!delivery) return null;

  const isOverdue =
    delivery.expected_by &&
    delivery.status !== "delivered" &&
    new Date(delivery.expected_by) < new Date();

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 className="mono">{delivery.order_id}</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{
              background: "none",
              border: "none",
              fontSize: "22px",
              cursor: "pointer",
              color: "var(--text-secondary)",
              lineHeight: 1,
            }}
          >
            ×
          </button>
        </div>

        {delivery.sync_status !== "pending" && (
          <div style={{ marginTop: "8px", display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
            <button
              className="btn-info-outline"
              onClick={() => {
                const link = `${window.location.origin}/?track=${delivery.id}`;
                navigator.clipboard.writeText(link);
              }}
            >
              Copy Customer Tracking Link
            </button>
            <button className="btn-info-outline" onClick={() => setShowQrCode(!showQrCode)}>
              {showQrCode ? "Hide" : "Show"} Package QR Code
            </button>
          </div>
        )}

        {showQrCode && (
          <div style={{ marginTop: "10px", textAlign: "center" }}>
            {/* Uses a free public QR-generation API (no library, no API key)
                to render a scannable code for this order — an agent's "Scan
                Package" button (native BarcodeDetector) can read this back. */}
            <img
              src={`https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(delivery.order_id)}`}
              alt={`QR code for ${delivery.order_id}`}
              width={160}
              height={160}
              style={{ backgroundColor: "#fff", padding: "8px", borderRadius: "var(--radius-sm)" }}
            />
            <div style={{ fontSize: "11.5px", color: "var(--text-muted)", marginTop: "4px" }}>
              Encodes: {delivery.order_id}
            </div>
          </div>
        )}

        <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "10px" }}>
          <DetailRow label="Status" value={<StatusBadge status={delivery.status} />} />
          {delivery.priority && delivery.priority !== "normal" && (
            <DetailRow
              label="Priority"
              value={
                <span style={{ color: (delivery.priority === "urgent" || delivery.priority === "high") ? "var(--danger)" : undefined, fontWeight: 600 }}>
                  {PRIORITY_LABELS[delivery.priority] || delivery.priority}
                </span>
              }
            />
          )}
          {agentName && <DetailRow label="Assigned Agent" value={agentName} />}
          {(delivery.customer_email || delivery.customer_phone) && (
            <DetailRow
              label="Customer"
              value={
                <span>
                  {delivery.customer_email || "—"}
                  {delivery.customer_email && delivery.customer_phone && " · "}
                  {delivery.customer_phone || ""}
                </span>
              }
            />
          )}
          {delivery.zone && <DetailRow label="Zone" value={delivery.zone} />}
          {delivery.expected_by && (
            <DetailRow
              label="Expected By"
              value={
                <span style={isOverdue ? { color: "var(--danger)", fontWeight: 600 } : undefined}>
                  {new Date(delivery.expected_by).toLocaleString()}
                  {isOverdue && " (Overdue)"}
                </span>
              }
            />
          )}
          {delivery.latitude && delivery.longitude && (
            <DetailRow label="Coordinates" value={`${delivery.latitude}, ${delivery.longitude}`} />
          )}
          <DetailRow label="Notes" value={delivery.notes || "—"} />
          <DetailRow label="Location Note" value={delivery.location_note || "—"} />
          {delivery.is_partial && (
            <DetailRow
              label="Partial Delivery"
              value={<span style={{ color: "var(--danger)" }}>{delivery.partial_notes || "Marked partially delivered"}</span>}
            />
          )}
          {delivery.reschedule_count > 0 && (
            <DetailRow
              label="Rescheduled"
              value={
                <span>
                  {delivery.reschedule_count}x
                  {delivery.rescheduled_to && ` — next attempt: ${new Date(delivery.rescheduled_to).toLocaleString()}`}
                  {delivery.reschedule_reason && ` (${delivery.reschedule_reason})`}
                </span>
              }
            />
          )}
          <DetailRow label="Created" value={new Date(delivery.created_at).toLocaleString()} />
          <DetailRow label="Last Updated" value={new Date(delivery.updated_at).toLocaleString()} />
          {delivery.sync_status && (
            <DetailRow
              label="Sync Status"
              value={delivery.sync_status === "synced" ? "Synced" : "Saved locally (not yet synced)"}
            />
          )}
        </div>

        <hr style={{ margin: "20px 0", border: "none", borderTop: "1px solid var(--border)" }} />

        <h4 style={{ marginBottom: "12px" }}>History</h4>

        {delivery.sync_status === "pending" && (
          <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            This delivery hasn't synced to the server yet, so its history log
            isn't available until it does.
          </p>
        )}

        {delivery.sync_status !== "pending" && isLoadingHistory && (
          <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>Loading history...</p>
        )}

        {delivery.sync_status !== "pending" && historyError && (
          <p style={{ color: "var(--danger)", fontSize: "13px" }}>{historyError}</p>
        )}

        {delivery.sync_status !== "pending" && !isLoadingHistory && !historyError && (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {history.map((entry) => (
              <div key={entry.id} style={{ borderLeft: "3px solid var(--accent)", paddingLeft: "10px" }}>
                <div style={{ fontSize: "13px", fontWeight: 600 }}>
                  {entry.old_status
                    ? `${STATUS_LABELS[entry.old_status] || entry.old_status} → ${STATUS_LABELS[entry.new_status] || entry.new_status}`
                    : `Created (${STATUS_LABELS[entry.new_status] || entry.new_status})`}
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                  by {entry.changed_by_display_name} · {new Date(entry.changed_at).toLocaleString()}
                </div>
                {entry.note && (
                  <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>{entry.note}</div>
                )}
              </div>
            ))}
          </div>
        )}

        <hr style={{ margin: "20px 0", border: "none", borderTop: "1px solid var(--border-color)" }} />

        <h4 style={{ marginBottom: "12px" }}>Delivery Attempts</h4>

        {delivery.sync_status === "pending" && (
          <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            This delivery hasn't synced to the server yet, so its attempt log isn't available until it does.
          </p>
        )}

        {delivery.sync_status !== "pending" && isLoadingAttempts && (
          <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>Loading attempts...</p>
        )}

        {delivery.sync_status !== "pending" && attemptsError && (
          <p style={{ color: "var(--danger)", fontSize: "13px" }}>{attemptsError}</p>
        )}

        {delivery.sync_status !== "pending" && !isLoadingAttempts && !attemptsError && (
          attempts.length === 0 ? (
            <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>No delivery attempts recorded yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {attempts.map((a) => (
                <div
                  key={a.id}
                  style={{
                    borderLeft: `3px solid ${a.outcome === "delivered" ? "var(--success, #16a34a)" : a.outcome === "partial_delivery" ? "var(--warning, #b45309)" : "var(--danger)"}`,
                    paddingLeft: "10px",
                  }}
                >
                  <div style={{ fontSize: "13px", fontWeight: 600 }}>
                    Attempt #{a.attempt_number}: {ATTEMPT_OUTCOME_LABELS[a.outcome] || a.outcome}
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                    {new Date(a.attempted_at).toLocaleString()}
                  </div>
                  {a.reason_label && (
                    <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>Reason: {a.reason_label}</div>
                  )}
                  {a.notes && (
                    <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>{a.notes}</div>
                  )}
                </div>
              ))}
            </div>
          )
        )}

        <hr style={{ margin: "20px 0", border: "none", borderTop: "1px solid var(--border-color)" }} />

        <h4 style={{ marginBottom: "12px" }}>Messages</h4>
        <DeliveryMessages
          deliveryId={delivery.id}
          isSyncedToServer={delivery.sync_status !== "pending"}
        />
      </div>
    </div>
  );
}

function DetailRow({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: "11px", color: "var(--text-secondary)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}>
        {label}
      </div>
      <div style={{ fontSize: "14px", marginTop: "2px" }}>{value}</div>
    </div>
  );
}
