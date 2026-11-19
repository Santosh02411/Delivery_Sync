/**
 * Sync engine: sends every locally-queued (offline) status update to
 * the backend's existing POST /sync endpoint, and reconciles the
 * results back into the local cache (./offlineStore.js).
 *
 * Mirrors frontend/src/services/syncEngine.js — same constants, same rules (./syncOutcome.js),
 * same runSync() control flow — with a different local-storage backend (AsyncStorage here vs
 * IndexedDB there) and a different "are we online" signal (no `navigator.onLine` in React Native —
 * see startAutoSync below).
 *
 * Every run ends in an explicit outcome — "success", "partial_success" or "failed" — and every record
 * the server refuses is CLASSIFIED instead of being left "pending" to be re-sent every 15 seconds forever:
 *   - permanent error (not assigned to you, no longer exists, invalid) -> BLOCKED: kept exactly as the
 *     agent left it, with a reason and recovery actions (try again / discard), never re-sent on its own;
 *   - retryable error -> the attempt is counted and shown; blocked after MAX_RECORD_ATTEMPTS;
 *   - a change the server did not apply (a newer change won) -> "conflicted" until acknowledged;
 *   - one invalid record that makes the server reject a whole batch (HTTP 422) is found and blocked
 *     without holding back the valid records behind it.
 * (There is no service worker on mobile, so nothing here needs "background re-registration": the 15s
 * foreground timer, the app-foregrounded event and the backoff below are the retry mechanism.)
 */

import * as Network from "expo-network";
import { AppState } from "react-native";
import {
  getPendingDeliveries,
  getCachedDeliveries,
  markAsSyncedIfUnchanged,
  markBlockedIfUnchanged,
  markRetryableFailureIfUnchanged,
  markConflictedIfUnchanged,
  retryBlockedRecord,
  acknowledgeConflict,
  discardLocalRecord,
} from "./offlineStore";
import { revisionOf } from "./syncRevision";
import { API_BASE_URL, getAccessToken, refreshAccessToken } from "./api";
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
const PERIODIC_SYNC_INTERVAL_MS = 15000; // check every 15 seconds while the app is foregrounded
const MAX_FOLLOW_UP_PASSES = 3; // extra passes allowed when edits land while a sync is in flight
const MAX_BACKOFF_MS = 5 * 60 * 1000; // when the server/network keeps failing, back off up to 5 minutes

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * POST /sync is AUTHENTICATED: the backend takes the agent's identity and
 * organization from the access token (never from the records' own
 * agent_id/org_id) and only accepts updates to deliveries assigned to
 * that agent. Access tokens last 30 minutes, so after a long offline
 * stretch the stored one is usually expired: on a 401 this refreshes the
 * session once and retries. If it still can't authenticate, it throws an
 * error with `status = 401` — the records stay queued locally (nothing is
 * lost) and sync resumes after the next successful login/refresh.
 */
async function postSync(records, token) {
  return fetch(`${API_BASE_URL}/sync`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    // client_sent_at lets the server correct event times recorded offline for a wrong device clock.
    body: JSON.stringify({ records, client_sent_at: new Date().toISOString() }),
  });
}

async function syncPendingDeliveries(records) {
  let response = await postSync(records, await getAccessToken());
  if (response.status === 401) {
    const fresh = await refreshAccessToken();
    if (fresh) response = await postSync(records, fresh);
  }
  if (!response.ok) {
    const error = new Error(response.status === 401 ? "Session expired — sign in again to sync." : "Sync request failed");
    error.status = response.status;
    // The server's explanation (FastAPI puts it in `detail`); the engine uses it to find the one record at fault.
    try {
      error.detail = (await response.json()).detail;
    } catch {
      error.detail = undefined;
    }
    throw error;
  }
  return response.json();
}

let syncRunning = false; // only one sync at a time in this app (a single JS runtime on mobile)

// ---------- Live state for the UI ("syncing" is never stored; it is "in flight right now") ----------

const syncingIds = new Set();
const listeners = new Set();

export function getSyncingIds() {
  return new Set(syncingIds);
}

/** Calls `listener()` whenever the sync state changes. Returns an unsubscribe function. */
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

/** { pending, syncing, synced, conflicted, blocked, total, retrying, needsAttention } for everything cached on this device. */
export async function getSyncSummary() {
  return summarizeSyncStates(await getCachedDeliveries(), syncingIds);
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
    acc.deferredCount += 1;
  }
}

/** The server refused the WHOLE request as invalid (400/413/422): find the bad record(s) and block only those. */
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
  const acknowledged = new Map((response.acknowledged || []).map((a) => [a.id, a.local_revision]));
  const conflicts = response.conflicts || [];
  const conflictsById = new Map(conflicts.filter((c) => c && c.id).map((c) => [c.id, c]));
  const linkedConflictIds = new Set();

  for (const resolvedRecord of response.resolved_records || []) {
    const revision = acknowledged.has(resolvedRecord.id) ? acknowledged.get(resolvedRecord.id) : submittedRevisions.get(resolvedRecord.id);
    const conflict = conflictsById.get(resolvedRecord.id);
    let outcome;
    if (conflict) {
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
  for (const conflict of conflicts) {
    if (!conflict || !linkedConflictIds.has(conflict.id)) acc.conflicts.push(conflict);
  }

  // Records the server REFUSED: classified, never left "pending" to be re-sent forever.
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
 * One upload attempt-loop — same logic as the web app's syncOnePass(): every attempt (and every
 * retry) re-reads the CURRENT pending queue, each record is sent with its `local_revision`, and
 * the server's answer is written back only if the record is still at that revision, so an edit
 * made while the request was in flight is never overwritten (see syncRevision.js).
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
      // Auth failures can't be fixed by retrying in 3 seconds — records
      // stay queued locally until a valid session exists again.
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
      total.requestFailed = true;
      total.error = result.error;
      if (result.authError) total.authError = true;
      break;
    }
    if (!result.deferredCount) break;
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
 * Attempts to sync all pending records. Retries on failure up to MAX_RETRIES times with a fixed
 * delay (same reasoning as the web app's runSync()). Only ONE sync runs at a time: a call made
 * while another is running returns { skipped: true } immediately — the records stay queued and
 * the next trigger picks them up. If edits land while a request is in flight, a bounded number of
 * follow-up passes uploads them right away instead of waiting for the next timer tick.
 *
 * Returns { outcome: "success" | "partial_success" | "failed", success, syncedCount, blockedCount,
 *           retryingCount, conflictCount, blocked[], retrying[], conflicts[], errors[], error?, authError? }
 * — or { outcome: "skipped", skipped: true }. `success` is true only for outcome "success".
 */
export async function runSync() {
  if (syncRunning) return { outcome: "skipped", success: true, syncedCount: 0, skipped: true };
  syncRunning = true;
  try {
    return await syncUntilQuiet();
  } finally {
    syncRunning = false;
    notifySyncState();
  }
}

// ---------- Recovery actions for blocked / conflicted records (what the UI buttons call) ----------

/** Puts a blocked record back in the queue (the next sync tick sends it). */
export async function retryBlocked(id) {
  const outcome = await retryBlockedRecord(id);
  notifySyncState();
  return outcome;
}

/** Removes a blocked record from THIS device (the server's data is untouched). */
export async function discardBlocked(id) {
  await discardLocalRecord(id);
  notifySyncState();
}

/** The agent has seen a conflict notice. */
export async function acknowledgeConflictNotice(id) {
  const outcome = await acknowledgeConflict(id);
  notifySyncState();
  return outcome;
}

/**
 * Sets up automatic syncing in THREE ways — same three as the web
 * app's startAutoSync(), adapted to React Native's actual signals:
 *
 * 1. Runs once immediately on startup, in case there are already
 *    pending records from a previous offline session.
 * 2. Runs again whenever the app is foregrounded (AppState "active") —
 *    React Native's closest equivalent to the browser's "online"
 *    event; there's no reliable cross-platform "connectivity just
 *    came back" push event the way a browser tab gets, but a user
 *    reopening/foregrounding the app after being offline is the
 *    moment that matters in practice, and expo-network's check inside
 *    triggerSync() means a foreground event while STILL offline
 *    correctly does nothing rather than wasting a request.
 * 3. Runs periodically every PERIODIC_SYNC_INTERVAL_MS while the app
 *    is in the foreground, so sync doesn't depend on the agent
 *    manually reopening a screen — same rationale as the web app's
 *    own periodic check.
 *
 * Call this once from App.js after the auth session is restored.
 */
export function startAutoSync(onSyncComplete) {
  let intervalId = null;
  let failureStreak = 0; // consecutive runs that failed at the REQUEST level (network / server down)
  let nextAllowedAt = 0;

  const triggerSync = async ({ force = false } = {}) => {
    // While the server keeps failing, wait longer between timer ticks (15s, 30s, 60s ... 5 min)
    // instead of hammering it. Foregrounding the app always tries immediately.
    if (!force && Date.now() < nextAllowedAt) return;
    const netState = await Network.getNetworkStateAsync();
    if (netState.isConnected && netState.isInternetReachable !== false) {
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
    }
  };

  const handleAppStateChange = (nextState) => {
    if (nextState === "active") {
      triggerSync({ force: true });
    }
  };

  // 1. Try once on startup
  triggerSync({ force: true });

  // 2. Try again whenever the app comes back to the foreground
  const subscription = AppState.addEventListener("change", handleAppStateChange);

  // 3. Try periodically in the background while foregrounded
  intervalId = setInterval(() => triggerSync(), PERIODIC_SYNC_INTERVAL_MS);

  // Return a cleanup function so the caller can stop auto-sync (e.g.
  // on logout) and avoid duplicate timers/listeners.
  return () => {
    subscription.remove();
    if (intervalId) clearInterval(intervalId);
  };
}
