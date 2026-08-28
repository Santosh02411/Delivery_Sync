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
    <div style={{ marginTop: "12px" }}>
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        {STATUS_OPTIONS.map((option) => {
          const isCurrent = option.value === currentStatus;
          return (
            <button
              key={option.value}
              onClick={() => handleStatusClick(option.value)}
              disabled={isCurrent}
              className={isCurrent ? "btn" : "btn btn-primary"}
            >
              {option.label}
            </button>
          );
        })}
      </div>

      <input
        type="text"
        className="input"
        placeholder="Optional note (e.g. customer not available)"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        style={{ marginTop: "10px", width: "100%" }}
      />
    </div>
  );
}
