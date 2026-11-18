/**
 * Sync engine: runs when the app comes online, sends all pending
 * (unsynced) records to the backend's /sync endpoint, and reconciles
 * the results back into IndexedDB.
 *
 * Every run ends in an explicit outcome — "success", "partial_success" or "failed" — and every
 * record the server refuses is CLASSIFIED instead of being left "pending" to be re-sent forever:
 *
 *   - permanent error (not assigned to you, no longer exists, invalid) -> the record is BLOCKED:
 *     moved out of the sync queue, kept exactly as the agent left it, with a reason and recovery
 *     actions (try again / discard). It is never re-sent on its own.
 *   - retryable error (concurrent update, unknown) -> the attempt is counted and shown; after
 *     MAX_RECORD_ATTEMPTS it becomes blocked too.
 *   - a change the server did not apply (a newer change won) -> "conflicted", kept visible until
 *     the agent acknowledges it.
 *   - one invalid record that makes the server reject a whole batch (HTTP 422) is found and blocked
 *     without holding back the valid records behind it.
 *
 * See syncOutcome.js for the rules and the state model. Includes retry logic for when a sync
 * attempt fails at the request level (e.g. connectivity drops mid-sync), a backoff in the timer
 * while the server keeps failing, and a request for the browser's Background Sync so retryable work
 * resumes even with every tab closed (public/sw.js).
 */

import {
  getPendingDeliveries,
  getAllLocalDeliveries,
  markAsSyncedIfUnchanged,
  markBlockedIfUnchanged,
  markRetryableFailureIfUnchanged,
  markConflictedIfUnchanged,
  retryBlockedRecord,
  acknowledgeConflict,
  deleteDeliveryLocally,
  getSyncLockName,
} from "./indexedDb";
import { revisionOf } from "./syncRevision";
import { syncPendingDeliveries } from "./api";
import { requestBackgroundSync } from "./backgroundSync";
import {
  OUTCOME,
  MAX_BATCH_SIZE,
  classifyRecordError,
  classifyRequestError,
  decideOutcome,
  extractRecordFaults,
  formatServerDetail,
  toSyncPayload,
  chunk,
  summarizeSyncStates,
} from "./syncOutcome";

// describeConflict lives with the other pure rules; re-exported because callers import it from here.
export { describeConflict } from "./syncOutcome";

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 3000;
const PERIODIC_SYNC_INTERVAL_MS = 15000; // check every 15 seconds while online
const MAX_FOLLOW_UP_PASSES = 3; // extra passes allowed when edits land while a sync is in flight
const MAX_BACKOFF_MS = 5 * 60 * 1000; // when the server/network keeps failing, back off up to 5 minutes

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const SKIPPED = Symbol("sync-skipped");
let inMemoryBusy = false; // fallback for browsers without the Web Locks API

// ---------- Live state for the UI ("syncing" is never stored; it is "in flight right now") ----------

const syncingIds = new Set();
const listeners = new Set();

/** Ids of the records being uploaded at this moment (a copy). */
export function getSyncingIds() {
  return new Set(syncingIds);
}

/** Calls `listener()` whenever the sync state changes (upload started/finished, recovery action taken). Returns an unsubscribe function. */
export function subscribeSyncState(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notifySyncState() {
  for (const listener of listeners) {
    try {
      listener();
    } catch (error) {
      console.warn("sync state listener failed:", error);
    }
  }
}

/** { pending, syncing, synced, conflicted, blocked, total, retrying, needsAttention } for everything stored on this device. */
export async function getSyncSummary() {
  return summarizeSyncStates(await getAllLocalDeliveries(), syncingIds);
}

/**
 * Runs `task` only if no other sync is running for this user — in this tab, in another tab,
 * or in the service worker's background sync (they all take the same named lock; see
 * getSyncLockName). Returns SKIPPED when another sync holds it. Nothing is lost by skipping:
 * the records stay pending and the next trigger (15s timer, "online" event, background sync)
 * picks them up.
 */
async function withSyncLock(task) {
  if (typeof navigator !== "undefined" && navigator.locks && navigator.locks.request) {
    return navigator.locks.request(getSyncLockName(), { ifAvailable: true }, (lock) => (lock ? task() : SKIPPED));
  }
  if (inMemoryBusy) return SKIPPED;
  inMemoryBusy = true;
  try {
    return await task();
  } finally {
    inMemoryBusy = false;
  }
}

function newAccumulator() {
  return { syncedCount: 0, errors: [], conflicts: [], blocked: [], retrying: [], deferredCount: 0 };
}

/** Quarantines one record (dead-letter) with its reason, unless the agent edited it again meanwhile. */
async function blockRecord(record, revision, info, acc) {
  const result = await markBlockedIfUnchanged(record.id, info, revision);
  if (result.applied) {
    acc.blocked.push({ id: record.id, order_id: record.order_id, code: info.code, message: info.message });
  } else if (result.reason === "changed_during_sync") {
    acc.deferredCount += 1; // a newer edit exists: it is uploaded by a follow-up pass instead
  }
}

/**
 * The server refused the WHOLE request as invalid (HTTP 400/413/422). That is deterministic — some record in
 * it is at fault — so waiting won't help and it must not hold back the valid records. Find the bad one(s):
 * use the positions the server names when it names them, otherwise split the batch in half and recurse.
 * A single record that is refused on its own is blocked, with the server's own explanation.
 */
async function isolateRejectedRecords(batch, error, acc) {
  if (batch.length === 1) {
    const message = formatServerDetail(error.detail) || `The server refused this record (HTTP ${error.status}).`;
    await blockRecord(batch[0], revisionOf(batch[0]), classifyRecordError({ code: "rejected_by_server", message }), acc);
    return;
  }

  const faults = extractRecordFaults(error.detail, batch.length);
  if (faults.length > 0 && faults.length < batch.length) {
    const bad = new Set(faults.map((f) => f.index));
    for (const fault of faults) {
      const record = batch[fault.index];
      await blockRecord(record, revisionOf(record), classifyRecordError({ code: "invalid_record", message: fault.message }), acc);
    }
    await submitBatch(batch.filter((_, index) => !bad.has(index)), acc);
    return;
  }

  const middle = Math.ceil(batch.length / 2);
  await submitBatch(batch.slice(0, middle), acc);
  await submitBatch(batch.slice(middle), acc);
}

/** Writes the server's answer for one batch back into local storage, record by record. */
async function applyResponse(batch, submittedRevisions, response, acc) {
  const byId = new Map(batch.map((r) => [r.id, r]));
  // Prefer the revision the SERVER says it answered; fall back to what we sent.
  const acknowledged = new Map((response.acknowledged || []).map((a) => [a.id, a.local_revision]));
  const conflicts = response.conflicts || [];
  const conflictsById = new Map(conflicts.filter((c) => c && c.id).map((c) => [c.id, c]));
  const linkedConflictIds = new Set();

  for (const resolvedRecord of response.resolved_records || []) {
    const revision = acknowledged.has(resolvedRecord.id) ? acknowledged.get(resolvedRecord.id) : submittedRevisions.get(resolvedRecord.id);
    const conflict = conflictsById.get(resolvedRecord.id);
    let outcome;
    if (conflict) {
      // The server did NOT apply this change. Keep ITS copy, but flag the record so the explanation
      // stays visible (and counted) until the agent has seen it — not just in a toast.
      outcome = await markConflictedIfUnchanged(resolvedRecord.id, resolvedRecord, conflict, revision);
      if (outcome.applied) {
        linkedConflictIds.add(conflict.id);
        acc.conflicts.push(conflict);
      }
    } else {
      outcome = await markAsSyncedIfUnchanged(resolvedRecord.id, resolvedRecord, revision);
      if (outcome.applied) acc.syncedCount += 1;
    }
    if (!outcome.applied && outcome.reason === "changed_during_sync") acc.deferredCount += 1;
  }
  // Conflicts that could not be tied to a stored record are still reported, never dropped.
  for (const conflict of conflicts) {
    if (!conflict || !linkedConflictIds.has(conflict.id)) acc.conflicts.push(conflict);
  }

  // Records the server REFUSED. Before, these were only counted and left "pending", so they were re-sent
  // every cycle forever. Now each one is classified: permanent -> blocked with a reason; retryable ->
  // attempt counted (and blocked once it keeps failing).
  for (const serverError of response.errors || []) {
    acc.errors.push(serverError);
    const record = byId.get(serverError.id);
    if (!record) continue;
    const revision = submittedRevisions.get(record.id);
    const info = classifyRecordError(serverError);
    if (info.kind === "permanent") {
      await blockRecord(record, revision, info, acc);
      continue;
    }
    const result = await markRetryableFailureIfUnchanged(record.id, info, revision);
    if (result.applied && result.blocked) {
      acc.blocked.push({ id: record.id, order_id: record.order_id, code: result.record.sync_error.code, message: result.record.sync_error.message });
    } else if (result.applied) {
      acc.retrying.push({ id: record.id, order_id: record.order_id, code: info.code, message: info.message, attempts: result.record.sync_attempts });
    } else if (result.reason === "changed_during_sync") {
      acc.deferredCount += 1;
    }
  }
}

/** Uploads one batch. A request-level failure (network, auth, server down) is thrown to the caller's retry loop. */
async function submitBatch(batch, acc) {
  if (batch.length === 0) return;
  const submittedRevisions = new Map(batch.map((r) => [r.id, revisionOf(r)]));
  batch.forEach((r) => syncingIds.add(r.id));
  notifySyncState();
  try {
    let response;
    try {
      response = await syncPendingDeliveries(batch.map(toSyncPayload));
    } catch (error) {
      if (classifyRequestError(error).scope === "rejected") {
        await isolateRejectedRecords(batch, error, acc);
        return;
      }
      throw error;
    }
    await applyResponse(batch, submittedRevisions, response, acc);
  } finally {
    batch.forEach((r) => syncingIds.delete(r.id));
    notifySyncState();
  }
}

/**
 * One upload attempt-loop. Every attempt (including each retry) re-reads the CURRENT pending
 * queue, so a retry never re-sends a stale snapshot over edits made in the meantime. Each
 * record is sent with its `local_revision`, and the server's answer is written back only if
 * the local record is still at that revision (see syncRevision.js for the full story).
 * Only "pending" records are read: blocked and conflicted ones are never re-sent.
 */
async function syncOnePass() {
  const acc = newAccumulator();
  let attempt = 0;

  while (attempt < MAX_RETRIES) {
    try {
      const pending = await getPendingDeliveries();
      for (const batch of chunk(pending, MAX_BATCH_SIZE)) {
        await submitBatch(batch, acc);
      }
      return { ...acc, requestFailed: false };
    } catch (error) {
      // 401/403: retrying within seconds can't help (the token is expired
      // or the account is blocked). Records stay pending; the next
      // periodic tick picks up whatever fresh token AuthContext has
      // stored by then. Nothing is lost.
      if (classifyRequestError(error).scope === "auth") {
        return { ...acc, requestFailed: true, authError: true, error: error.message };
      }
      attempt += 1;
      if (attempt >= MAX_RETRIES) {
        return { ...acc, requestFailed: true, error: error.message };
      }
      await wait(RETRY_DELAY_MS);
    }
  }
}

/** Keeps going while edits keep landing mid-flight (bounded), so a newer edit isn't left waiting for the next timer tick. */
async function syncUntilQuiet() {
  const total = { ...newAccumulator(), requestFailed: false };

  for (let pass = 0; pass <= MAX_FOLLOW_UP_PASSES; pass += 1) {
    const result = await syncOnePass();
    total.syncedCount += result.syncedCount;
    total.errors.push(...result.errors);
    total.conflicts.push(...result.conflicts);
    total.blocked.push(...result.blocked);
    total.retrying.push(...result.retrying);
    if (result.requestFailed) {
      // Report the failure, but don't hide what earlier passes already achieved.
      total.requestFailed = true;
      total.error = result.error;
      if (result.authError) total.authError = true;
      break;
    }
    if (!result.deferredCount) break; // nothing changed while we were uploading: done
  }

  const outcome = decideOutcome({
    syncedCount: total.syncedCount,
    conflictCount: total.conflicts.length,
    blockedCount: total.blocked.length,
    retryingCount: total.retrying.length,
    requestFailed: total.requestFailed,
  });

  return {
    outcome,
    success: outcome === OUTCOME.SUCCESS, // true ONLY when nothing needs attention
    syncedCount: total.syncedCount,
    blockedCount: total.blocked.length,
    retryingCount: total.retrying.length,
    conflictCount: total.conflicts.length,
    errorCount: total.blocked.length + total.retrying.length,
    errors: total.errors,
    conflicts: total.conflicts,
    blocked: total.blocked,
    retrying: total.retrying,
    ...(total.requestFailed ? { requestFailed: true, error: total.error } : {}),
    ...(total.authError ? { authError: true } : {}),
  };
}

/**
 * Attempts to sync all pending records. Only ONE sync runs at a time per user (across tabs and
 * the service worker); a call made while another is running returns { skipped: true } at once.
 *
 * Returns { outcome: "success" | "partial_success" | "failed", success: boolean, syncedCount,
 *           blockedCount, retryingCount, conflictCount, blocked[], retrying[], conflicts[], errors[],
 *           error?, authError? } — or { outcome: "skipped", skipped: true } if another sync was running.
 * `success` is true only for outcome "success": a run in which the server refused records is NOT a success.
 */
export async function runSync() {
  const result = await withSyncLock(syncUntilQuiet);
  if (result === SKIPPED) return { outcome: "skipped", success: true, syncedCount: 0, skipped: true };

  // Something is left that a later attempt can still fix (network/server problem, or a record being
  // retried): ask the browser to wake the service worker and try again, even if every tab is closed.
  const retryableWorkLeft = result.retryingCount > 0 || (result.requestFailed && !result.authError);
  if (retryableWorkLeft) requestBackgroundSync();

  notifySyncState();
  return result;
}

// ---------- Recovery actions for blocked / conflicted records (what the UI buttons call) ----------

/** Puts a blocked record back in the queue and syncs it again right away. */
export async function retryBlocked(id) {
  const outcome = await retryBlockedRecord(id);
  notifySyncState();
  return outcome;
}

/** Removes a blocked record from THIS device (the server's data is untouched). */
export async function discardBlocked(id) {
  await deleteDeliveryLocally(id);
  notifySyncState();
}

/** The agent has seen a conflict notice. */
export async function acknowledgeConflictNotice(id) {
  const outcome = await acknowledgeConflict(id);
  notifySyncState();
  return outcome;
}

/**
 * Sets up automatic syncing in THREE ways:
 * 1. Runs once immediately on startup, in case there are already pending records
 * 2. Runs again whenever the browser's "online" event fires (e.g. wifi reconnects)
 * 3. Runs periodically every PERIODIC_SYNC_INTERVAL_MS while online, so sync
 *    doesn't depend on the component remounting (e.g. switching views or
 *    refreshing the page) — this is what makes the "Online — changes will
 *    sync automatically" banner text actually true at all times.
 *
 * Call this once when the app loads.
 */
export function startAutoSync(onSyncComplete) {
  let failureStreak = 0; // consecutive runs that failed at the REQUEST level (network / server down)
  let nextAllowedAt = 0;

  const triggerSync = async ({ force = false } = {}) => {
    if (!navigator.onLine) return;
    // While the server/network keeps failing, wait longer between timer ticks (15s, 30s, 60s ... 5 min)
    // instead of hammering it. Connectivity coming back ("online" event) always tries immediately.
    if (!force && Date.now() < nextAllowedAt) return;

    const result = await runSync();
    if (!result.skipped) {
      if (result.requestFailed && !result.authError) {
        failureStreak += 1;
        nextAllowedAt = Date.now() + Math.min(PERIODIC_SYNC_INTERVAL_MS * 2 ** failureStreak, MAX_BACKOFF_MS);
      } else {
        failureStreak = 0;
        nextAllowedAt = 0;
      }
    }
    if (onSyncComplete) onSyncComplete(result);
  };

  const onOnline = () => triggerSync({ force: true });

  // 1. Try once on startup
  triggerSync();

  // 2. Try again whenever connectivity is restored
  window.addEventListener("online", onOnline);

  // 3. Try periodically in the background while online
  const intervalId = setInterval(() => triggerSync(), PERIODIC_SYNC_INTERVAL_MS);

  // Return a cleanup function so the caller can stop auto-sync
  // (e.g. if the component unmounts) and avoid memory leaks / duplicate timers
  return () => {
    window.removeEventListener("online", onOnline);
    clearInterval(intervalId);
  };
}
