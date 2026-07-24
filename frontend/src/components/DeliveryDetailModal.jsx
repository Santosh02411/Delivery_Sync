import React, { useEffect, useState } from "react";
import { fetchDeliveryHistory } from "../services/api";
import { useAuth } from "../context/AuthContext";

const STATUS_LABELS = {
  picked_up: "Picked Up",
  out_for_delivery: "Out for Delivery",
  delivered: "Delivered",
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

  useEffect(() => {
    if (!delivery) return;
    loadHistory();
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

  if (!delivery) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          backgroundColor: "white",
          borderRadius: "10px",
          padding: "24px",
          width: "100%",
          maxWidth: "480px",
          maxHeight: "80vh",
          overflowY: "auto",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <h3 style={{ margin: 0 }}>{delivery.order_id}</h3>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              fontSize: "20px",
              cursor: "pointer",
            }}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div
          style={{
            marginTop: "16px",
            display: "flex",
            flexDirection: "column",
            gap: "10px",
          }}
        >
          <DetailRow
            label="Status"
            value={STATUS_LABELS[delivery.status] || delivery.status}
          />
          {agentName && <DetailRow label="Assigned Agent" value={agentName} />}
          <DetailRow label="Notes" value={delivery.notes || "—"} />
          <DetailRow
            label="Location Note"
            value={delivery.location_note || "—"}
          />
          <DetailRow
            label="Created"
            value={new Date(delivery.created_at).toLocaleString()}
          />
          <DetailRow
            label="Last Updated"
            value={new Date(delivery.updated_at).toLocaleString()}
          />
          {delivery.sync_status && (
            <DetailRow
              label="Sync Status"
              value={
                delivery.sync_status === "synced"
                  ? "Synced"
                  : "Saved locally (not yet synced)"
              }
            />
          )}
        </div>

        <hr
          style={{
            margin: "20px 0",
            border: "none",
            borderTop: "1px solid #eee",
          }}
        />

        <h4 style={{ marginBottom: "12px" }}>History</h4>

        {delivery.sync_status === "pending" && (
          <p style={{ color: "#888", fontSize: "13px" }}>
            This delivery hasn't synced to the server yet, so its history log
            isn't available until it does.
          </p>
        )}

        {delivery.sync_status !== "pending" && isLoadingHistory && (
          <p style={{ color: "#888", fontSize: "13px" }}>Loading history...</p>
        )}

        {delivery.sync_status !== "pending" && historyError && (
          <p style={{ color: "#c62828", fontSize: "13px" }}>{historyError}</p>
        )}

        {delivery.sync_status !== "pending" &&
          !isLoadingHistory &&
          !historyError && (
            <div
              style={{ display: "flex", flexDirection: "column", gap: "12px" }}
            >
              {history.map((entry) => (
                <div
                  key={entry.id}
                  style={{
                    borderLeft: "3px solid #1565c0",
                    paddingLeft: "10px",
                  }}
                >
                  <div style={{ fontSize: "13px", fontWeight: 600 }}>
                    {entry.old_status
                      ? `${STATUS_LABELS[entry.old_status] || entry.old_status} → ${STATUS_LABELS[entry.new_status] || entry.new_status}`
                      : `Created (${STATUS_LABELS[entry.new_status] || entry.new_status})`}
                  </div>
                  <div style={{ fontSize: "12px", color: "#666" }}>
                    by {entry.changed_by_display_name} ·{" "}
                    {new Date(entry.changed_at).toLocaleString()}
                  </div>
                  {entry.note && (
                    <div style={{ fontSize: "12px", color: "#888" }}>
                      {entry.note}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
      </div>
    </div>
  );
}

function DetailRow({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: "12px", color: "#888", fontWeight: 600 }}>
        {label}
      </div>
      <div style={{ fontSize: "14px" }}>{value}</div>
    </div>
  );
}
