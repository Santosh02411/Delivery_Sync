import React, { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { fetchCustomerDeliveryAgentLocation } from "../services/api";

// Leaflet's default marker icon references image files by a relative
// path that doesn't survive bundling — this is the standard fix,
// pointing it at the same images the leaflet package ships.
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

/**
 * Live GPS tracking map for one delivery — polls the agent's real
 * current position every 8s and moves the marker. Uses OpenStreetMap
 * tiles (free, no API key) via Leaflet, not Google Maps, since Google
 * Maps requires a billing-enabled API key.
 */
export default function LiveTrackingMap({ token, deliveryId }) {
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const markerRef = useRef(null);
  const [status, setStatus] = useState("loading"); // loading | live | unavailable

  useEffect(() => {
    let intervalId;
    let cancelled = false;

    async function poll() {
      try {
        const loc = await fetchCustomerDeliveryAgentLocation(token, deliveryId);
        if (cancelled) return;

        if (!loc) {
          setStatus((prev) => (prev === "live" ? "live" : "unavailable"));
          return;
        }

        setStatus("live");
        const latLng = [loc.latitude, loc.longitude];

        if (!mapRef.current && mapContainerRef.current) {
          mapRef.current = L.map(mapContainerRef.current, {
            zoomControl: true,
            attributionControl: true,
          }).setView(latLng, 14);

          L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
          }).addTo(mapRef.current);

          markerRef.current = L.marker(latLng).addTo(mapRef.current)
            .bindPopup("Your delivery agent");
        } else if (mapRef.current && markerRef.current) {
          markerRef.current.setLatLng(latLng);
          mapRef.current.panTo(latLng);
        }
      } catch (err) {
        if (!cancelled) setStatus("unavailable");
      }
    }

    poll();
    intervalId = setInterval(poll, 8000);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, [token, deliveryId]);

  if (status === "unavailable") {
    return (
      <p style={{ fontSize: "12px", color: "var(--text-muted)" }}>
        Live location isn't available for this order yet — it shows up
        once the agent turns on location sharing.
      </p>
    );
  }

  return (
    <div>
      <div
        ref={mapContainerRef}
        style={{ height: "220px", width: "100%", borderRadius: "var(--radius-sm)", overflow: "hidden" }}
      />
      {status === "loading" && (
        <p style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>Locating agent...</p>
      )}
    </div>
  );
}
