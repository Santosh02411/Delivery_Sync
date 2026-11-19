/**
 * AsyncStorage-based local delivery cache — the mobile equivalent of
 * the web app's frontend/src/services/indexedDb.js, same conceptual
 * model, different storage API (React Native has no IndexedDB):
 *
 * - Every delivery successfully fetched from the server is cached here
 *   with sync_status "synced", so the delivery list/detail screens can
 *   still show *something* real when offline, instead of a blank
 *   error screen.
 * - A status update made while offline (or one whose network request
 *   just failed) is applied to the LOCAL cached copy immediately
 *   (sync_status "pending") so the UI reflects the change right away,
 *   and queued for the sync engine (./offlineSync.js) to actually send
 *   once connectivity returns.
 *
 * Same per-user scoping rationale as indexedDb.js's setActiveUser: if
 * two different agents ever log into the same physical device, agent
 * B must never see agent A's leftover cached/pending deliveries the
 * moment they log in — the storage key itself is scoped to the
 * logged-in user's id.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import { nextRevision, decideSyncedWrite, decideServerRecordWrite } from "./syncRevision";
import {
  decideBlockedWrite, decideRetryableFailureWrite, decideConflictWrite,
  decideRetryBlocked, decideAcknowledgeConflict, withoutLocalSyncFields, newEventId,
} from "./syncOutcome";

let activeUserId = null;

export function setActiveUser(userId) {
  activeUserId = userId;
}

function storageKey() {
  if (!activeUserId) {
    throw new Error("setActiveUser() must be called before using offline storage.");
  }
  return `delivery_sync_deliveries_${activeUserId}`;
}

async function readAll() {
  if (!activeUserId) return {};
  const raw = await AsyncStorage.getItem(storageKey());
  return raw ? JSON.parse(raw) : {};
}

async function writeAll(recordsById) {
  await AsyncStorage.setItem(storageKey(), JSON.stringify(recordsById));
}

/**
 * Serializes every read-modify-write on the store. This whole cache lives under ONE AsyncStorage
 * key, so each change is "read the whole map, edit it, write the whole map back". Two of those
 * overlapping (a status edit while a sync response is being applied, say) would each write back a
 * map that is missing the other's change — a lost update that no revision number can undo.
 * Running them one after another through this queue makes each one see the previous one's result.
 */
let storeQueue = Promise.resolve();
function withStoreLock(task) {
  const run = storeQueue.then(task, task); // start after the previous task, whether it succeeded or not
  storeQueue = run.catch(() => {});         // a failed task must not jam the queue
  return run;
}

/**
 * Bulk-caches a freshly-fetched list of deliveries as "synced" —
 * called after every successful GET /deliveries/mine or GET
 * /deliveries/{id}, so the most recent known-good server state is
 * always available locally. Never overwrites a "pending" local
 * record with server data for the SAME id — that would silently
 * discard an offline edit that hasn't synced yet (the same "local
 * pending change wins until synced" rule indexedDb.js's own
 * saveDeliveryLocally already applies).
 */
export async function cacheDeliveries(deliveries) {
  return withStoreLock(async () => {
    const existing = await readAll();
    for (const delivery of deliveries) {
      const decision = decideServerRecordWrite(existing[delivery.id], delivery);
      if (decision.write) existing[delivery.id] = decision.record;
    }
    await writeAll(existing);
  });
}

export async function getCachedDeliveries() {
  const all = await readAll();
  return Object.values(all);
}

export async function getCachedDelivery(id) {
  const all = await readAll();
  return all[id] || null;
}

/**
 * Applies a status change to the local cached copy and marks it
 * "pending" — called when an authenticated PATCH /deliveries/{id}
 * request fails due to no connectivity (see api.js's
 * updateDeliveryStatus). The full existing cached record is required
 * as a base because the eventual sync to POST /sync needs a complete
 * record (agent_id, org_id, created_at, zone, etc. — see
 * backend/app/routes/sync.py's SyncRecordIn), not just the one
 * changed field.
 */
export async function queueStatusUpdate(deliveryId, patch) {
  return withStoreLock(async () => {
    const all = await readAll();
    const current = all[deliveryId];
    if (!current) {
      throw new Error("Can't queue an offline update for a delivery that was never cached.");
    }
    // Every local edit bumps local_revision (see syncRevision.js) so a sync response for an
    // OLDER version of this record can never overwrite this newer edit.
    // A fresh edit also starts a clean slate: an earlier "blocked" / "conflicted" / retry state belonged to the
    // OLD revision, so it is cleared and this edit gets its own chance to sync.
    const updated = { ...withoutLocalSyncFields(current), ...patch, sync_status: "pending", local_revision: nextRevision(current), event_id: newEventId() };
    all[deliveryId] = updated;
    await writeAll(all);
    return updated;
  });
}

export async function getPendingDeliveries() {
  const all = await readAll();
  return Object.values(all).filter((d) => d.sync_status === "pending");
}

export async function getPendingCount() {
  const pending = await getPendingDeliveries();
  return pending.length;
}

/**
 * Reconciles a local record with the server's final resolved version after a sync — same role as
 * indexedDb.js's markAsSyncedIfUnchanged. Stores the SERVER's version (if a conflict was resolved
 * in favor of someone else's more recent change, the cache must reflect that) — but only if the
 * record is still at `submittedRevision`, i.e. the agent didn't edit it again while the request
 * was in flight. Resolves to { applied, reason? }.
 */
export async function markAsSyncedIfUnchanged(id, resolvedRecord, submittedRevision) {
  return withStoreLock(async () => {
    const all = await readAll();
    const decision = decideSyncedWrite(all[id], resolvedRecord, submittedRevision);
    if (!decision.write) return { applied: false, reason: decision.reason };
    all[id] = decision.record;
    await writeAll(all);
    return { applied: true };
  });
}

/**
 * Read the record, let a pure decision function (syncOutcome.js) say what to write, write it — all inside
 * the store lock, so nothing can slip in between. Resolves to { applied, reason?, blocked? }.
 */
async function applyDecision(id, decide) {
  return withStoreLock(async () => {
    const all = await readAll();
    const decision = decide(all[id]);
    if (!decision.write) return { applied: false, reason: decision.reason };
    all[id] = decision.record;
    await writeAll(all);
    return { applied: true, blocked: Boolean(decision.blocked), record: decision.record };
  });
}

/** The server refused this revision for good: quarantine it with its reason (the dead-letter queue). */
export function markBlockedIfUnchanged(id, errorInfo, submittedRevision) {
  return applyDecision(id, (current) => decideBlockedWrite(current, errorInfo, submittedRevision));
}

/** A retryable failure for this record: counts the attempt; blocks it after too many. */
export function markRetryableFailureIfUnchanged(id, errorInfo, submittedRevision) {
  return applyDecision(id, (current) => decideRetryableFailureWrite(current, errorInfo, submittedRevision));
}

/** The server did not apply this change: store ITS copy, flagged "conflicted", with the explanation. */
export function markConflictedIfUnchanged(id, resolvedRecord, conflict, submittedRevision) {
  return applyDecision(id, (current) => decideConflictWrite(current, resolvedRecord, conflict, submittedRevision));
}

/** Recovery: put a blocked record back into the sync queue. */
export function retryBlockedRecord(id) {
  return applyDecision(id, decideRetryBlocked);
}

/** Recovery: the agent has seen a conflict notice. */
export function acknowledgeConflict(id) {
  return applyDecision(id, decideAcknowledgeConflict);
}

/** Recovery: remove a record from THIS device only (the server's data is untouched). */
export async function discardLocalRecord(id) {
  return withStoreLock(async () => {
    const all = await readAll();
    delete all[id];
    await writeAll(all);
  });
}

/** Wiped on logout — see AuthContext.js's logout(). */
export async function clearAll() {
  if (!activeUserId) return;
  return withStoreLock(() => AsyncStorage.removeItem(storageKey()));
}
