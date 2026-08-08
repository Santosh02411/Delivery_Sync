import React, { useState } from "react";
import { buildSuggestedRoute } from "../services/routeOptimizer";
import StatusBadge from "./StatusBadge";

/**
 * Shows the agent's active (not-delivered) deliveries grouped by zone and,
 * where coordinates are available, ordered via nearest-neighbor — see
 * services/routeOptimizer.js for the algorithm itself.
 *
 * Uses the browser's geolocation API (with the agent's permission) as the
 * starting point for route ordering. If permission is denied or
 * geolocation isn't available, falls back to (0, 0) as a starting point —
 * the ordering is still internally consistent (deliveries get ordered
 * relative to each other), just not anchored to the agent's real position.
 * This is disclosed in the UI rather than silently guessing.
 */
export default function SuggestedRoute({ deliveries }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [startPoint, setStartPoint] = useState(null);
  const [locationStatus, setLocationStatus] = useState("idle"); // idle | requesting | granted | denied | unavailable

  const activeDeliveries = deliveries.filter(
    (d) => d.status === "picked_up" || d.status === "out_for_delivery"
  );

  function requestLocation() {
    if (!navigator.geolocation) {
      setLocationStatus("unavailable");
      setStartPoint({ lat: 0, lon: 0 });
      return;
    }

    setLocationStatus("requesting");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setStartPoint({ lat: position.coords.latitude, lon: position.coords.longitude });
        setLocationStatus("granted");
      },
      () => {
        setLocationStatus("denied");
        setStartPoint({ lat: 0, lon: 0 });
      },
      { timeout: 8000 }
    );
  }

  function handleToggleExpand() {
    if (!isExpanded && !startPoint) {
      requestLocation();
    }
    setIsExpanded(!isExpanded);
  }

  if (activeDeliveries.length === 0) {
    return null; // nothing active to route — don't show an empty widget
  }

  const route = startPoint ? buildSuggestedRoute(activeDeliveries, startPoint) : [];
  const anyHasCoords = activeDeliveries.some((d) => d.latitude && d.longitude);

  return (
    <div className="card" style={{ marginBottom: "20px" }}>
      <div
        onClick={handleToggleExpand}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }}
      >
        <h3 style={{ margin: 0 }}>Suggested Route ({activeDeliveries.length} active)</h3>
        <span style={{ color: "var(--accent)", fontSize: "13px" }}>
          {isExpanded ? "Hide" : "Show"}
        </span>
      </div>

      {isExpanded && (
        <div style={{ marginTop: "14px" }}>
          {locationStatus === "requesting" && (
            <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
              Requesting your location to order nearby deliveries first...
            </p>
          )}

          {locationStatus === "denied" && (
            <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>
              Location permission wasn't granted, so the route below is grouped by
              zone but not anchored to your current position.
            </p>
          )}

          {locationStatus === "unavailable" && (
            <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>
              Location isn't available on this device/browser, so the route below
              is grouped by zone only.
            </p>
          )}

          {!anyHasCoords && (
            <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>
              None of your active deliveries have coordinates yet, so they're
              grouped by zone in the order they were assigned. Ask your
              dispatcher to add coordinates for a precisely-ordered route.
            </p>
          )}

          {route.map((zoneGroup) => (
            <div key={zoneGroup.zone} style={{ marginTop: "14px" }}>
              <div style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: "6px" }}>
                {zoneGroup.zone}
              </div>
              {zoneGroup.deliveries.map((d, index) => (
                <div
                  key={d.id}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    padding: "8px 10px",
                    borderRadius: "var(--radius-sm)",
                    backgroundColor: "var(--bg-input)",
                    marginBottom: "6px",
                  }}
                >
                  <span>
                    <strong className="mono">{index + 1}.</strong>{" "}
                    <span className="mono">{d.order_id}</span>
                  </span>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    {d._distanceFromPreviousKm !== undefined && (
                      <span style={{ fontSize: "11.5px", color: "var(--text-muted)" }}>
                        {d._distanceFromPreviousKm.toFixed(1)} km
                      </span>
                    )}
                    <StatusBadge status={d.status} />
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
