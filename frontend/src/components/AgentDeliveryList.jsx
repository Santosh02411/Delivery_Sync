import React, { useEffect, useState } from "react";
import {
  saveDeliveryLocally,
  getAllLocalDeliveries,
  deleteDeliveryLocally,
  mergeAssignedDeliveries,
  setActiveUser,
} from "../services/indexedDb";
import { startAutoSync, runSync } from "../services/syncEngine";
import { deleteDeliveryOnServer, fetchMyDeliveriesFromServer, updateMyAgentLocation } from "../services/api";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import DeliveryStatusUpdater from "./DeliveryStatusUpdater";
import SyncStatusBadge from "./SyncStatusBadge";
import StatusBadge from "./StatusBadge";
import DeliveryDetailModal from "./DeliveryDetailModal";
import Pagination from "./Pagination";
import SuggestedRoute from "./SuggestedRoute";
import ProofOfDeliveryModal from "./ProofOfDeliveryModal";
import BarcodeScannerModal from "./BarcodeScannerModal";

const PULL_INTERVAL_MS = 15000; // check for newly-assigned deliveries every 15s while online
const PAGE_SIZE = 5;

/**
 * Main view for the delivery agent. Deliveries come from TWO sources,
 * merged together locally:
 * 1. Deliveries a dispatcher assigned (pulled from the server)
 * 2. Any local status updates the agent makes, saved to IndexedDB first
 *    and pushed up via the sync engine
 */
export default function AgentDeliveryList() {
  const { token, user } = useAuth();
  const { showToast } = useToast();
  const [deliveries, setDeliveries] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedDelivery, setSelectedDelivery] = useState(null);
  const [pendingDelivered, setPendingDelivered] = useState(null);
  const [showScanner, setShowScanner] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [isSharingLocation, setIsSharingLocation] = useState(false);
  const [locationError, setLocationError] = useState(null);
  const watchIdRef = React.useRef(null);

  useEffect(() => {
    // CRITICAL: must happen before any IndexedDB read/write below. Scopes
    // local storage to THIS specific user, so switching accounts on the
    // same browser can never show one agent another agent's deliveries.
    setActiveUser(user.id);

    loadFromLocalStorage();
    pullAssignedDeliveries();

    const stopAutoSync = startAutoSync((result) => {
      if (result.success && result.syncedCount > 0) {
        loadFromLocalStorage();
      }
    });

    const pullIntervalId = setInterval(() => {
      if (navigator.onLine) pullAssignedDeliveries();
    }, PULL_INTERVAL_MS);

    return () => {
      stopAutoSync();
      clearInterval(pullIntervalId);
    };
  }, []);

  // Reset to page 1 whenever the search query changes, so a new search
  // doesn't leave you stranded on a page that no longer has results
  useEffect(() => {
    setCurrentPage(1);
  }, [searchQuery]);

  async function pullAssignedDeliveries() {
    if (!navigator.onLine) return;
    try {
      const assigned = await fetchMyDeliveriesFromServer(token);
      await mergeAssignedDeliveries(assigned);
      await loadFromLocalStorage();
    } catch (error) {
      console.warn("Could not pull assigned deliveries:", error.message);
      showToast(`Couldn't check for new assignments: ${error.message}`, "error");
    }
  }

  async function loadFromLocalStorage() {
    const records = await getAllLocalDeliveries();
    const sorted = records.sort(
      (a, b) => new Date(a.created_at) - new Date(b.created_at)
    );
    setDeliveries(sorted);
  }

  async function handleStatusUpdate(deliveryId, newStatus, notes) {
    if (newStatus === "delivered") {
      // Don't save yet — require proof of delivery first. The actual
      // save happens in handleProofConfirm once the agent provides it.
      setPendingDelivered({ deliveryId, notes });
      return;
    }
    await applyStatusUpdate(deliveryId, newStatus, notes, null);
  }

  async function applyStatusUpdate(deliveryId, newStatus, notes, proofOfDelivery) {
    const existing = deliveries.find((d) => d.id === deliveryId);
    const now = new Date().toISOString();

    const updatedRecord = {
      ...existing,
      status: newStatus,
      notes: notes || existing?.notes || "",
      updated_at: now,
      ...(proofOfDelivery ? { proof_of_delivery: proofOfDelivery } : {}),
    };

    await saveDeliveryLocally(updatedRecord);
    await loadFromLocalStorage();
  }

  async function handleProofConfirm(proofDataUrl) {
    const { deliveryId, notes } = pendingDelivered;
    setPendingDelivered(null);
    await applyStatusUpdate(deliveryId, "delivered", notes, proofDataUrl);
    showToast("Delivery confirmed with proof.", "success");
  }

  async function handleDelete(delivery) {
    const confirmed = window.confirm(
      `Delete ${delivery.order_id}? This cannot be undone.`
    );
    if (!confirmed) return;

    await deleteDeliveryLocally(delivery.id);

    if (delivery.sync_status === "synced") {
      try {
        await deleteDeliveryOnServer(token, delivery.id);
      } catch (error) {
        console.warn("Could not delete on server (may be offline):", error.message);
      }
    }

    showToast(`Deleted ${delivery.order_id}.`, "success");
    await loadFromLocalStorage();
  }

  function toggleLocationSharing() {
    if (isSharingLocation) {
      if (watchIdRef.current !== null) {
        navigator.geolocation.clearWatch(watchIdRef.current);
        watchIdRef.current = null;
      }
      setIsSharingLocation(false);
      return;
    }

    if (!navigator.geolocation) {
      setLocationError("Location isn't available on this device/browser.");
      return;
    }

    setLocationError(null);
    watchIdRef.current = navigator.geolocation.watchPosition(
      async (position) => {
        try {
          await updateMyAgentLocation(token, position.coords.latitude, position.coords.longitude);
        } catch (err) {
          console.warn("Failed to push location update:", err.message);
        }
      },
      (err) => {
        setLocationError(`Couldn't get your location: ${err.message}`);
        setIsSharingLocation(false);
      },
      { enableHighAccuracy: true, maximumAge: 15000, timeout: 20000 }
    );
    setIsSharingLocation(true);
  }

  useEffect(() => {
    // Stop sharing if the agent navigates away from this screen entirely
    return () => {
      if (watchIdRef.current !== null) navigator.geolocation.clearWatch(watchIdRef.current);
    };
  }, []);

  async function handleManualSync() {
    const result = await runSync();
    if (result.success) {
      if (result.syncedCount > 0) {
        showToast(`Synced ${result.syncedCount} record(s).`, "success");
      } else {
        showToast("Already up to date.", "info");
      }
      if (result.errorCount > 0) {
        showToast(
          `${result.errorCount} record(s) couldn't sync — they may not belong to your account.`,
          "error"
        );
      }
      // CRITICAL: reload the UI from IndexedDB directly, unconditionally —
      // this is a pure local read and needs no network at all. The
      // previous version called pullAssignedDeliveries() here instead,
      // which ALSO tries to fetch new assignments from the server and
      // bails out early if navigator.onLine is false. That early return
      // was silently skipping the local-only UI refresh too, so a
      // successful sync (which DID update IndexedDB to "synced" — the
      // toast above was accurate) could still show stale "Saved locally"
      // badges on screen if the browser reported offline at that exact
      // moment. Splitting these two concerns fixes that: the local
      // reload always happens, and pulling new assignments is attempted
      // separately as a best-effort, network-dependent step.
      await loadFromLocalStorage();
      await pullAssignedDeliveries();
    } else {
      showToast(`Sync failed: ${result.error}`, "error");
    }
  }

  const filteredDeliveries = deliveries.filter((d) =>
    d.order_id.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const totalPages = Math.max(1, Math.ceil(filteredDeliveries.length / PAGE_SIZE));
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const visibleDeliveries = filteredDeliveries.slice(pageStart, pageStart + PAGE_SIZE);

  return (
    <div>
      <h2 className="page-title">My Deliveries</h2>

      <SuggestedRoute deliveries={deliveries} />

      <div style={{ display: "flex", gap: "8px", marginBottom: "20px", flexWrap: "wrap" }}>
        <button className="btn btn-primary" onClick={handleManualSync}>
          Sync Now
        </button>
        <button className="btn" onClick={() => setShowScanner(true)}>
          Scan Package
        </button>
        <button
          className="btn"
          onClick={toggleLocationSharing}
          style={isSharingLocation ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined}
        >
          {isSharingLocation ? "📍 Sharing Location" : "📍 Share My Location"}
        </button>
        <input
          type="text"
          className="input"
          placeholder="Search by order ID..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ flexGrow: 1, minWidth: "200px" }}
        />
      </div>

      {locationError && (
        <p style={{ color: "var(--danger)", fontSize: "12px", marginTop: "-12px", marginBottom: "16px" }}>
          {locationError}
        </p>
      )}

      {showScanner && (
        <BarcodeScannerModal
          onScan={(value) => {
            setSearchQuery(value);
            setShowScanner(false);
            showToast(`Scanned: ${value}`, "success");
          }}
          onClose={() => setShowScanner(false)}
        />
      )}

      {filteredDeliveries.length === 0 && deliveries.length === 0 && (
        <p style={{ color: "var(--text-secondary)" }}>
          No deliveries assigned yet. Check back soon, or ask your dispatcher.
        </p>
      )}
      {filteredDeliveries.length === 0 && deliveries.length > 0 && (
        <p style={{ color: "var(--text-secondary)" }}>No deliveries match "{searchQuery}".</p>
      )}

      {visibleDeliveries.map((delivery) => (
        <div key={delivery.id} className="delivery-card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="delivery-card-order-id">{delivery.order_id}</span>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <SyncStatusBadge status={delivery.sync_status} />
              <button className="btn-info-outline" onClick={() => setSelectedDelivery(delivery)}>
                Details
              </button>
              <button className="btn-danger-outline" onClick={() => handleDelete(delivery)}>
                Delete
              </button>
            </div>
          </div>

          <div style={{ marginTop: "8px", display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
            <StatusBadge status={delivery.status} />
            {delivery.zone && (
              <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                Zone: {delivery.zone}
              </span>
            )}
            {(delivery.customer_email || delivery.customer_phone) && (
              <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                {[delivery.customer_email, delivery.customer_phone].filter(Boolean).join(" · ")}
              </span>
            )}
            {delivery.expected_by && (() => {
              const overdue = delivery.status !== "delivered" && new Date(delivery.expected_by) < new Date();
              return (
                <span style={{ fontSize: "12px", color: overdue ? "var(--danger)" : "var(--text-secondary)", fontWeight: overdue ? 600 : 400 }}>
                  Due: {new Date(delivery.expected_by).toLocaleString()}{overdue && " (Overdue)"}
                </span>
              );
            })()}
          </div>

          {delivery.notes && (
            <p style={{ marginTop: "8px", fontSize: "13px", color: "var(--text-secondary)" }}>
              {delivery.notes}
            </p>
          )}

          <DeliveryStatusUpdater
            deliveryId={delivery.id}
            currentStatus={delivery.status}
            onUpdate={handleStatusUpdate}
          />
        </div>
      ))}

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        onPageChange={setCurrentPage}
        totalItems={filteredDeliveries.length}
        pageSize={PAGE_SIZE}
      />

      {selectedDelivery && (
        <DeliveryDetailModal
          delivery={selectedDelivery}
          onClose={() => setSelectedDelivery(null)}
        />
      )}

      {pendingDelivered && (
        <ProofOfDeliveryModal
          onConfirm={handleProofConfirm}
          onCancel={() => setPendingDelivered(null)}
        />
      )}
    </div>
  );
}
