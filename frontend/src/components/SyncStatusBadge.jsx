import React from "react";

export default function SyncStatusBadge({ status }) {
  const isSynced = status === "synced";

  const style = {
    display: "inline-block",
    padding: "2px 10px",
    borderRadius: "12px",
    fontSize: "12px",
    fontWeight: "bold",
    color: "white",
    backgroundColor: isSynced ? "#2e7d32" : "#f9a825",
  };

  return <span style={style}>{isSynced ? "Synced" : "Saved locally"}</span>;
}
