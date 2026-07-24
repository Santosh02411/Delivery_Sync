import React, { useEffect, useState } from "react";
import {
  saveDeliveryLocally,
  getAllLocalDeliveries,
  deleteDeliveryLocally,
  mergeAssignedDeliveries,
} from "../services/indexedDb";
import { startAutoSync, runSync } from "../services/syncEngine";
import {
  deleteDeliveryOnServer,
  fetchMyDeliveriesFromServer,
} from "../services/api";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import DeliveryStatusUpdater from "./DeliveryStatusUpdater";
import SyncStatusBadge from "./SyncStatusBadge";
import DeliveryDetailModal from "./DeliveryDetailModal";

const PULL_INTERVAL_MS = 15000; // check for newly-assigned deliveries every 15s while online

/**
 * Main view for the delivery agent. Deliveries now come from TWO sources,
 * merged together locally:
 * 1. Deliveries a dispatcher assigned (pulled from the server, see
 *    fetchMyDeliveriesFromServer / mergeAssignedDeliveries)
 * 2. Any local status updates the agent makes, saved to IndexedDB first
 *    and pushed up via the sync engine
 */
export default function AgentDeliveryList() {
  const { token } = useAuth();
  const { showToast } = useToast();
  const [deliveries, setDeliveries] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedDelivery, setSelectedDelivery] = useState(null);

  useEffect(() => {
    loadFromLocalStorage();
    pullAssignedDeliveries();

    // Push-sync: sends local pending changes up (on load, on reconnect, periodically)
    const stopAutoSync = startAutoSync((result) => {
      if (result.success && result.syncedCount > 0) {
        loadFromLocalStorage();
      }
    });

    // Pull-sync: fetches newly assigned deliveries down, periodically while online
    const pullIntervalId = setInterval(() => {
      if (navigator.onLine) pullAssignedDeliveries();
    }, PULL_INTERVAL_MS);

    return () => {
      stopAutoSync();
      clearInterval(pullIntervalId);
    };
  }, []);

  async function pullAssignedDeliveries() {
    if (!navigator.onLine) return;
    try {
      const assigned = await fetchMyDeliveriesFromServer(token);
      await mergeAssignedDeliveries(assigned);
      await loadFromLocalStorage();
    } catch (error) {
      // Silent failure is fine here — this runs automatically in the
      // background, and the agent's existing local data is unaffected.
      console.warn("Could not pull assigned deliveries:", error.message);
    }
  }

  async function loadFromLocalStorage() {
    const records = await getAllLocalDeliveries();
    // Sort by CREATED date (oldest first), not updated date. Sorting by
    // "most recently updated" caused a confusing experience for agents:
    // the delivery they just touched would immediately jump to the top of
    // the list. Sorting by creation order keeps each delivery's position
    // stable regardless of what status changes happen to it.
    const sorted = records.sort(
      (a, b) => new Date(a.created_at) - new Date(b.created_at),
    );
    setDeliveries(sorted);
  }

  async function handleStatusUpdate(deliveryId, newStatus, notes) {
    const existing = deliveries.find((d) => d.id === deliveryId);
    const now = new Date().toISOString();

    const updatedRecord = {
      ...existing,
      status: newStatus,
      notes: notes || existing?.notes || "",
      updated_at: now,
    };

    await saveDeliveryLocally(updatedRecord);
    await loadFromLocalStorage();
  }

  async function handleDelete(delivery) {
    const confirmed = window.confirm(
      `Delete ${delivery.order_id}? This cannot be undone.`,
    );
    if (!confirmed) return;

    await deleteDeliveryLocally(delivery.id);

    if (delivery.sync_status === "synced") {
      try {
        await deleteDeliveryOnServer(token, delivery.id);
      } catch (error) {
        console.warn(
          "Could not delete on server (may be offline):",
          error.message,
        );
      }
    }

    showToast(`Deleted ${delivery.order_id}.`, "success");
    await loadFromLocalStorage();
  }

  async function handleManualSync() {
    const result = await runSync();
    if (result.success) {
      if (result.syncedCount > 0) {
        showToast(`Synced ${result.syncedCount} record(s).`, "success");
      } else {
        showToast("Already up to date.", "info");
      }
      await pullAssignedDeliveries();
    } else {
      showToast(`Sync failed: ${result.error}`, "error");
    }
  }

  const visibleDeliveries = deliveries.filter((d) =>
    d.order_id.toLowerCase().includes(searchQuery.toLowerCase()),
  );

  return (
    <div style={{ padding: "16px" }}>
      <h2>My Deliveries</h2>

      <div
        style={{
          display: "flex",
          gap: "8px",
          marginBottom: "16px",
          flexWrap: "wrap",
        }}
      >
        <button onClick={handleManualSync}>Sync Now</button>
        <input
          type="text"
          placeholder="Search by order ID..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ padding: "6px 10px", flexGrow: 1, minWidth: "200px" }}
        />
      </div>

      {visibleDeliveries.length === 0 && deliveries.length === 0 && (
        <p>
          No deliveries assigned yet. Check back soon, or ask your dispatcher.
        </p>
      )}
      {visibleDeliveries.length === 0 && deliveries.length > 0 && (
        <p>No deliveries match "{searchQuery}".</p>
      )}

      {visibleDeliveries.map((delivery) => (
        <div
          key={delivery.id}
          style={{
            border: "1px solid #ddd",
            borderRadius: "8px",
            padding: "12px",
            marginBottom: "12px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <strong>{delivery.order_id}</strong>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <SyncStatusBadge status={delivery.sync_status} />
              <button
                onClick={() => setSelectedDelivery(delivery)}
                style={{
                  background: "none",
                  border: "1px solid #1565c0",
                  color: "#1565c0",
                  borderRadius: "4px",
                  padding: "2px 8px",
                  cursor: "pointer",
                  fontSize: "12px",
                }}
              >
                Details
              </button>
              <button
                onClick={() => handleDelete(delivery)}
                style={{
                  background: "none",
                  border: "1px solid #c62828",
                  color: "#c62828",
                  borderRadius: "4px",
                  padding: "2px 8px",
                  cursor: "pointer",
                  fontSize: "12px",
                }}
              >
                Delete
              </button>
            </div>
          </div>
          <p>Status: {delivery.status}</p>
          {delivery.notes && <p>Note: {delivery.notes}</p>}

          <DeliveryStatusUpdater
            deliveryId={delivery.id}
            currentStatus={delivery.status}
            onUpdate={handleStatusUpdate}
          />
        </div>
      ))}

      {selectedDelivery && (
        <DeliveryDetailModal
          delivery={selectedDelivery}
          onClose={() => setSelectedDelivery(null)}
        />
      )}
    </div>
  );
}
