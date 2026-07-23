import React from "react";
import { useConnectivity } from "../hooks/useConnectivity";

export default function ConnectivityBanner() {
  const isOnline = useConnectivity();

  const style = {
    padding: "8px 16px",
    textAlign: "center",
    fontWeight: "bold",
    color: "white",
    backgroundColor: isOnline ? "#2e7d32" : "#c62828",
  };

  return (
    <div style={style}>
      {isOnline ? "Online — changes will sync automatically" : "Offline — changes are being saved locally"}
    </div>
  );
}
