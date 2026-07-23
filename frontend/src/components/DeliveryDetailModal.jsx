import React from "react";

const STATUS_LABELS = {
  picked_up: "Picked Up",
  out_for_delivery: "Out for Delivery",
  delivered: "Delivered",
  failed_attempt: "Failed Attempt",
};

/**
 * Modal showing full details of a single delivery record: order info,
 * status, notes, and timestamps. Shared between the Agent and Dispatcher
 * views so clicking any delivery card/row opens the same detail view.
 *
 * NOTE: a full status-change history/audit log (who changed what, when)
 * is a separate planned feature — this modal currently shows the record's
 * current state only, not its change history over time.
 */
export default function DeliveryDetailModal({ delivery, agentName, onClose }) {
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
          maxWidth: "440px",
          maxHeight: "80vh",
          overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0 }}>{delivery.order_id}</h3>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", fontSize: "20px", cursor: "pointer" }}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "10px" }}>
          <DetailRow label="Status" value={STATUS_LABELS[delivery.status] || delivery.status} />
          {agentName && <DetailRow label="Assigned Agent" value={agentName} />}
          <DetailRow label="Notes" value={delivery.notes || "—"} />
          <DetailRow label="Location Note" value={delivery.location_note || "—"} />
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
              value={delivery.sync_status === "synced" ? "Synced" : "Saved locally (not yet synced)"}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function DetailRow({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: "12px", color: "#888", fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: "14px" }}>{value}</div>
    </div>
  );
}
