/**
 * IndexedDB wrapper for offline delivery record storage.
 *
 * This is the client-side "source of truth" while offline. Every write from
 * the Agent view goes here FIRST, never directly to the server.
 *
 * We use the raw IndexedDB API (no external library) so it's easy to explain
 * line-by-line in an interview.
 *
 * IMPORTANT: the database is scoped PER USER (see setActiveUser below).
 * Without this, IndexedDB is shared by browser origin only — meaning if
 * two different agents log in on the same browser (a common real-world
 * case: a shared device, or simply testing two accounts), Agent B would
 * see Agent A's leftover local records the moment they logged in, since
 * nothing in the app distinguished "whose" local data it was. Call
 * setActiveUser(user.id) once, right after login, before any other
 * function in this file is used.
 */

const DB_VERSION = 2;
const STORE_NAME = "deliveries";
const PINGS_STORE = "location_pings";
const MAX_QUEUED_PINGS = 30; // caps how large a gap of missed pings we'll try to replay — old enough entries get dropped rather than growing unbounded over a long offline stretch

import { requestBackgroundSync } from "./backgroundSync";
import { nextRevision, decideSyncedWrite, decideServerRecordWrite } from "./syncRevision";
import {
  decideBlockedWrite, decideRetryableFailureWrite, decideConflictWrite,
  decideRetryBlocked, decideAcknowledgeConflict, withoutLocalSyncFields, newEventId,
} from "./syncOutcome";

let activeDbName = null;

/**
 * Must be called once a user is known to be logged in (their id from
 * AuthContext), before any other function in this module runs. Every
 * subsequent open/read/write in this file uses a database name scoped to
 * that specific user, so switching accounts on the same browser can never
 * leak one user's local records into another's view.
 */
export function setActiveUser(userId) {
  activeDbName = `delivery_sync_db_${userId}`;
}

/**
 * Name of the cross-tab lock that guards syncing for the active user. The web page, other
 * tabs, and the service worker (public/sw.js builds the identical string) all take this same
 * lock, so only one of them uploads a given user's queue at a time.
 */
export function getSyncLockName() {
  return `delivery-sync:${activeDbName}`;
}

/**
 * Opens (or creates) the IndexedDB database and the object store.
 * Returns a Promise that resolves to the open database connection.
 */
function openDb() {
  if (!activeDbName) {
    return Promise.reject(
      new Error("setActiveUser() must be called before using IndexedDB storage.")
    );
  }

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(activeDbName, DB_VERSION);

    // Runs only the first time, or when DB_VERSION is bumped
    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: "id" });
        store.createIndex("sync_status", "sync_status", { unique: false });
      }
      if (!db.objectStoreNames.contains(PINGS_STORE)) {
        db.createObjectStore(PINGS_STORE, { keyPath: "id", autoIncrement: true });
      }
    };

    request.onsuccess = (event) => resolve(event.target.result);
    request.onerror = (event) => reject(event.target.error);
  });
}

/**
 * Saves (creates or updates) a delivery record locally.
 * Always sets sync_status to "pending" since it's a local-only change
 * until it's confirmed synced with the server.
 */
export async function saveDeliveryLocally(record) {
  const db = await openDb();
  const saved = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    // Read-then-write inside ONE transaction, so two quick edits can never be given the same
    // revision. The revision comes from what is stored, never from the (possibly stale)
    // record object the caller passed in.
    const getRequest = store.get(record.id);
    getRequest.onsuccess = () => {
      // A fresh local edit starts a clean slate: any earlier "blocked" / "conflicted" / retry bookkeeping
      // belonged to the OLD revision, so it is cleared and the edit gets its own chance to sync.
      const recordToSave = { ...withoutLocalSyncFields(record), sync_status: "pending", local_revision: nextRevision(getRequest.result), event_id: newEventId() };
      const putRequest = store.put(recordToSave);
      putRequest.onsuccess = () => resolve(recordToSave);
      putRequest.onerror = (event) => reject(event.target.error);
    };
    getRequest.onerror = (event) => reject(event.target.error);
  });

  // Ask the browser to wake the service worker and sync this the moment
  // connectivity is detected — even if every tab gets closed before that
  // happens.
  requestBackgroundSync();

  return saved;
}

/**
 * Retrieves all delivery records stored locally.
 */
export async function getAllLocalDeliveries() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const request = store.getAll();

    request.onsuccess = () => resolve(request.result);
    request.onerror = (event) => reject(event.target.error);
  });
}

/**
 * Retrieves only records that haven't been synced yet.
 * This is what the sync engine calls when connectivity returns.
 */
export async function getPendingDeliveries() {
  const all = await getAllLocalDeliveries();
  return all.filter((record) => record.sync_status === "pending");
}

/**
 * Marks a local record as synced after the server answered for the version we SENT.
 * Stores the server's resolved version (it may differ because of conflict resolution) — but
 * ONLY if the record is still at `submittedRevision`. If the agent edited it again while the
 * request was in flight, the newer edit is kept as "pending" and nothing is overwritten.
 *
 * The compare and the write happen in a single readwrite transaction, so nothing can slip in
 * between them. Resolves to { applied: boolean, reason?: string }.
 */
export async function markAsSyncedIfUnchanged(id, resolvedRecord, submittedRevision) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    const getRequest = store.get(id);
    getRequest.onsuccess = () => {
      const decision = decideSyncedWrite(getRequest.result, resolvedRecord, submittedRevision);
      if (!decision.write) {
        resolve({ applied: false, reason: decision.reason });
        return;
      }
      const putRequest = store.put(decision.record);
      putRequest.onsuccess = () => resolve({ applied: true });
      putRequest.onerror = (event) => reject(event.target.error);
    };
    getRequest.onerror = (event) => reject(event.target.error);
  });
}

/**
 * Generic "read the record, let a pure decision function say what to write, write it" — all in ONE
 * readwrite transaction, so nothing can slip in between the check and the write. Resolves to
 * { applied, reason?, blocked? } exactly like markAsSyncedIfUnchanged.
 */
function applyDecision(id, decide) {
  return openDb().then(
    (db) =>
      new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readwrite");
        const store = tx.objectStore(STORE_NAME);
        const getRequest = store.get(id);
        getRequest.onsuccess = () => {
          const decision = decide(getRequest.result);
          if (!decision.write) {
            resolve({ applied: false, reason: decision.reason });
            return;
          }
          const putRequest = store.put(decision.record);
          putRequest.onsuccess = () => resolve({ applied: true, blocked: Boolean(decision.blocked), record: decision.record });
          putRequest.onerror = (event) => reject(event.target.error);
        };
        getRequest.onerror = (event) => reject(event.target.error);
      })
  );
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
  return applyDecision(id, decideRetryBlocked).then((outcome) => {
    if (outcome.applied) requestBackgroundSync();
    return outcome;
  });
}

/** Recovery: the agent has seen a conflict notice. */
export function acknowledgeConflict(id) {
  return applyDecision(id, decideAcknowledgeConflict);
}

/**
 * Stores a record that came FROM the server (not one we just uploaded), unless there is an
 * unsynced local edit for it — checked inside the same transaction as the write, so an edit
 * made a moment ago can't be clobbered by an older server copy.
 */
export async function storeServerRecord(id, serverRecord) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    const getRequest = store.get(id);
    getRequest.onsuccess = () => {
      const decision = decideServerRecordWrite(getRequest.result, serverRecord);
      if (!decision.write) {
        resolve({ applied: false, reason: decision.reason });
        return;
      }
      const putRequest = store.put(decision.record);
      putRequest.onsuccess = () => resolve({ applied: true });
      putRequest.onerror = (event) => reject(event.target.error);
    };
    getRequest.onerror = (event) => reject(event.target.error);
  });
}

/**
 * Permanently deletes a delivery record from local IndexedDB storage.
 */
export async function deleteDeliveryLocally(id) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    const request = store.delete(id);

    request.onsuccess = () => resolve();
    request.onerror = (event) => reject(event.target.error);
  });
}

/**
 * Merges deliveries assigned by a dispatcher (fetched from the server)
 * into local IndexedDB, WITHOUT overwriting any record the agent has
 * already touched locally and hasn't synced yet.
 *
 * Why this matters: if an agent already changed a delivery's status while
 * offline (sync_status: "pending"), blindly overwriting it with the
 * server's older version would silently lose that change. So this only
 * inserts records that are either brand new locally, or already marked
 * "synced" (meaning there's no unsynced local edit to protect).
 */
export async function mergeAssignedDeliveries(serverRecords) {
  for (const serverRecord of serverRecords) {
    // storeServerRecord checks "is there an unsynced local edit?" INSIDE the write transaction
    // (the old version read everything first and wrote later, so an edit made in between was
    // lost). It never marks the record "pending", so a freshly-pulled record isn't re-uploaded.
    await storeServerRecord(serverRecord.id, serverRecord);
  }
}

// ---------- Live GPS ping queue ----------
// When an agent's device has no signal, a location update to the server
// currently just fails. Instead of losing it silently, it's queued here
// and replayed (oldest first) the moment connectivity returns — see
// services/locationSyncEngine.js. Capped at MAX_QUEUED_PINGS so a long
// stretch offline doesn't grow this unboundedly; only the most recent
// pings matter for a live map anyway.

export async function queueLocationPing(latitude, longitude) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(PINGS_STORE, "readwrite");
    const store = tx.objectStore(PINGS_STORE);
    store.add({ latitude, longitude, queued_at: new Date().toISOString() });

    tx.oncomplete = async () => {
      // Trim to the cap: drop the oldest entries first if we've grown past it.
      const all = await getQueuedLocationPings();
      if (all.length > MAX_QUEUED_PINGS) {
        const toDrop = all.slice(0, all.length - MAX_QUEUED_PINGS);
        for (const ping of toDrop) await removeLocationPing(ping.id);
      }
      requestBackgroundSync();
      resolve();
    };
    tx.onerror = (event) => reject(event.target.error);
  });
}

export async function getQueuedLocationPings() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(PINGS_STORE, "readonly");
    const request = tx.objectStore(PINGS_STORE).getAll();
    request.onsuccess = () => resolve(request.result.sort((a, b) => a.id - b.id));
    request.onerror = (event) => reject(event.target.error);
  });
}

export async function removeLocationPing(pingId) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(PINGS_STORE, "readwrite");
    tx.objectStore(PINGS_STORE).delete(pingId);
    tx.oncomplete = () => resolve();
    tx.onerror = (event) => reject(event.target.error);
  });
}
