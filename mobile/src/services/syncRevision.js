/**
 * Revision rules that stop a sync response from overwriting a newer local edit.
 *
 * THE BUG THIS PREVENTS (a classic lost update):
 *   1. The agent marks a parcel "delivered"      -> local record, revision 1, pending
 *   2. A sync starts and sends revision 1
 *   3. While the request is in the air the agent edits it again   -> revision 2, pending
 *   4. The response for revision 1 arrives. Writing it into the local store would replace
 *      revision 2 with an older server answer and mark it "synced": the second edit would
 *      silently vanish and never be uploaded.
 *
 * THE RULE: every local edit bumps `local_revision`. A sync remembers the revision it SENT.
 * When the answer comes back it is only written if the stored record is still at that same
 * revision. If the record changed meanwhile it stays "pending" (at its newer revision) and is
 * simply uploaded again on the next pass.
 *
 * Pure functions (no storage access). This is the mobile copy of frontend/src/services/syncRevision.js
 * (the two apps are separate packages and can't import each other) — keep them identical.
 */

/** Records created before this field existed count as revision 0. */
export function revisionOf(record) {
  return Number.isInteger(record?.local_revision) && record.local_revision >= 0 ? record.local_revision : 0;
}

/** Revision for the next local edit of `current` (the record as it is stored right now). */
export function nextRevision(current) {
  return revisionOf(current) + 1;
}

/**
 * What to do with the server's answer for a record we sent at `submittedRevision`.
 * `current` is the record as stored RIGHT NOW (read in the same transaction as the write).
 * Returns { write: false, reason } or { write: true, record }.
 */
export function decideSyncedWrite(current, resolved, submittedRevision) {
  if (!current) {
    // Deleted locally while the request was in flight: don't bring it back from the dead.
    return { write: false, reason: "deleted_locally" };
  }
  if (current.sync_status !== "pending") {
    // Someone else already settled this record (another tab / the service worker): nothing to do.
    return { write: false, reason: "already_synced" };
  }
  if (revisionOf(current) !== submittedRevision) {
    return { write: false, reason: "changed_during_sync" };
  }
  return { write: true, record: { ...resolved, sync_status: "synced", local_revision: revisionOf(current) } };
}

/**
 * What to do with a record pulled FROM the server (e.g. the agent's assigned list). Never
 * replaces an unsynced local edit — including one made after we started fetching.
 */
export function decideServerRecordWrite(current, serverRecord) {
  if (current && current.sync_status === "pending") {
    return { write: false, reason: "local_edit_pending" };
  }
  return { write: true, record: { ...serverRecord, sync_status: "synced", local_revision: revisionOf(current) } };
}
