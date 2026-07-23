import React, { useState } from "react";

const STATUS_OPTIONS = [
  { value: "picked_up", label: "Picked Up" },
  { value: "out_for_delivery", label: "Out for Delivery" },
  { value: "delivered", label: "Delivered" },
  { value: "failed_attempt", label: "Failed Attempt" },
];

/**
 * Lets the agent change a delivery's status and add an optional note.
 * Calls `onUpdate` with the new status/notes — the parent component
 * (AgentDeliveryList) is responsible for actually saving it to IndexedDB.
 */
export default function DeliveryStatusUpdater({ deliveryId, currentStatus, onUpdate }) {
  const [notes, setNotes] = useState("");

  const handleStatusClick = (newStatus) => {
    onUpdate(deliveryId, newStatus, notes);
    setNotes("");
  };

  return (
    <div style={{ marginTop: "8px" }}>
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        {STATUS_OPTIONS.map((option) => (
          <button
            key={option.value}
            onClick={() => handleStatusClick(option.value)}
            disabled={option.value === currentStatus}
            style={{
              padding: "10px 14px",
              borderRadius: "6px",
              border: "none",
              cursor: option.value === currentStatus ? "default" : "pointer",
              backgroundColor: option.value === currentStatus ? "#ccc" : "#1565c0",
              color: option.value === currentStatus ? "#666" : "white",
            }}
          >
            {option.label}
          </button>
        ))}
      </div>

      <input
        type="text"
        placeholder="Optional note (e.g. customer not available)"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        style={{ marginTop: "8px", width: "100%", padding: "8px" }}
      />
    </div>
  );
}
