import React from "react";
import { SYNC_STATE, syncStateLabel } from "../services/syncOutcome";

/**
 * One badge for every state a record can be in: Saved locally (pending), Syncing…, Synced,
 * Conflict, Blocked. `syncing` is true while the record is being uploaded right now; `title` is the
 * tooltip (the reason, for blocked / conflicted / retrying records).
 */
export default function SyncStatusBadge({ status, syncing = false, title }) {
  const state = status === SYNC_STATE.PENDING && syncing ? SYNC_STATE.SYNCING : status || SYNC_STATE.SYNCED;
  const known = [SYNC_STATE.PENDING, SYNC_STATE.SYNCING, SYNC_STATE.SYNCED, SYNC_STATE.CONFLICTED, SYNC_STATE.BLOCKED];
  const cssState = known.includes(state) ? state : SYNC_STATE.PENDING;
  return (
    <span className={`sync-badge ${cssState}`} title={title || undefined}>
      {syncStateLabel(cssState)}
    </span>
  );
}
