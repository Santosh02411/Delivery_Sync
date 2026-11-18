/**
 * Service worker: makes the app installable and lets its SHELL (HTML,
 * JS, CSS, fonts) load even with no connection — NOT a replacement for
 * this app's existing offline-data logic (IndexedDB + the sync engine),
 * which already handles actual delivery data completely separately.
 * This only concerns itself with the static files the app is built from.
 *
 * Deliberately uses a RUNTIME caching strategy (cache whatever gets
 * fetched, as it's fetched) rather than a hardcoded list of files to
 * pre-cache on install. A hand-written hardcoded list would go stale the
 * moment Vite's production build renames its output files (they include
 * a content hash, e.g. index-a1b2c3.js) — runtime caching adapts
 * automatically instead of needing to be kept in sync with the build.
 *
 * Explicitly does NOT cache API requests (anything to the FastAPI
 * backend) — that data has its own, more correct offline story already
 * (IndexedDB + conflict-resolved sync), and letting a service worker
 * cache-and-serve stale API responses on top of that would just
 * introduce a second, conflicting source of "offline truth."
 */

const CACHE_NAME = "delivery-sync-shell-v1";
const API_ORIGIN = "http://127.0.0.1:8000";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never intercept API calls — always go straight to the network,
  // exactly as if this service worker didn't exist for these requests.
  if (url.origin === API_ORIGIN) {
    return;
  }

  // Only handle GET requests for same-origin app-shell files
  if (event.request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }

  if (event.request.mode === "navigate") {
    // HTML page loads: try the network first (so you always get the
    // latest version while online), falling back to the cached shell
    // if there's no connection.
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request).then((cached) => cached || caches.match("/")))
    );
    return;
  }

  // Static assets (JS, CSS, fonts, images): cache-first, since content-hashed
  // filenames mean a cached copy is always still valid.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((response) => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return response;
      });
    })
  );
});

/**
 * Real Web Push handling. When the backend sends a push (see
 * backend/app/services/push.py), the browser wakes this service worker
 * up — even if the app/tab is completely closed — and this fires,
 * showing a genuine OS-level notification (Windows notification center,
 * macOS Notification Center, Android's notification shade, etc.).
 */
self.addEventListener("push", (event) => {
  let payload = { title: "Delivery Sync", body: "You have an update.", url: "/" };
  try {
    if (event.data) payload = event.data.json();
  } catch (err) {
    // If the payload isn't JSON for some reason, fall back to the default above.
  }

  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: payload.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window" }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      return self.clients.openWindow(targetUrl);
    })
  );
});

/**
 * Real Background Sync — the browser wakes this service worker and
 * fires 'sync' the moment it detects connectivity, even if every tab
 * of the app is closed. Same class of mechanism as the 'push' handler
 * above (service-worker-driven, works with the tab closed), applied to
 * outgoing sync instead of incoming push.
 *
 * This is a plain, unbundled script (see the file-level docstring
 * above), so it can't `import` the app's ES module sync engines
 * (services/syncEngine.js etc.) — those only exist for the main thread.
 * Instead this re-implements the same core replay logic directly with
 * raw IndexedDB + fetch, reading "who's logged in" from the shared
 * context record written by services/backgroundSyncContext.js.
 *
 * If no context is found (nobody's logged in on this device, or the
 * context is stale), this is a no-op — there's nothing it could
 * meaningfully sync without a token.
 */

function swOpenDb(name, version) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, version);
    request.onsuccess = (event) => resolve(event.target.result);
    request.onerror = (event) => reject(event.target.error);
    // Deliberately no onupgradeneeded here — this handler only ever
    // reads/writes stores the main thread already created. If the
    // database doesn't exist yet at all, onsuccess still fires (it
    // gets created empty), and the subsequent transaction below will
    // simply find no matching object store and fail gracefully.
  });
}

/**
 * Opens a database ONLY if it already exists; resolves null if it doesn't. Plain swOpenDb
 * creates an empty database as a side effect, and an empty database at the current version
 * means the main thread's own upgrade (which creates the stores) never runs afterwards.
 * "This account has no such queue" is a normal, non-error situation — null means "nothing to do".
 */
function swOpenExistingDb(name, version) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(name, version);
    let missing = false;
    request.onupgradeneeded = (event) => {
      missing = true; // an upgrade means it didn't exist yet (or is older): the main thread owns the schema
      event.target.transaction.abort();
    };
    request.onsuccess = (event) => resolve(event.target.result);
    request.onerror = (event) => {
      if (missing) {
        event.preventDefault();
        resolve(null);
      } else {
        reject(event.target.error);
      }
    };
  });
}

function swGetSyncContext() {
  return swOpenDb("app_sync_context", 1).then(
    (db) =>
      new Promise((resolve) => {
        if (!db.objectStoreNames.contains("context")) {
          resolve(null);
          return;
        }
        const tx = db.transaction("context", "readonly");
        const request = tx.objectStore("context").get("current");
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => resolve(null);
      })
  ).catch(() => null);
}

function swGetAll(db, storeName) {
  return new Promise((resolve, reject) => {
    if (!db.objectStoreNames.contains(storeName)) {
      resolve([]);
      return;
    }
    const tx = db.transaction(storeName, "readonly");
    const request = tx.objectStore(storeName).getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = (event) => reject(event.target.error);
  });
}

function swDelete(db, storeName, key) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readwrite");
    tx.objectStore(storeName).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = (event) => reject(event.target.error);
  });
}

function swPut(db, storeName, value) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, "readwrite");
    tx.objectStore(storeName).put(value);
    tx.oncomplete = () => resolve();
    tx.onerror = (event) => reject(event.target.error);
  });
}

// --- Sync states, error classification and outcomes (mirrors src/services/syncOutcome.js) ---
// A service worker can't import app modules, so these few pure rules are repeated here. They are
// checked against the app's copy by src/services/__tests__/syncOutcome.test.js — change both together.
//
// In short: every record the server refuses is CLASSIFIED instead of being left "pending" to be re-sent
// on every sync forever. Permanent -> "blocked" (kept, with a reason, never re-sent on its own).
// Retryable -> attempt counted, blocked after SW_MAX_RECORD_ATTEMPTS. A change the server did not apply
// -> "conflicted". A failed REQUEST is retried by the browser (see the 'sync' handler at the bottom).

const SW_MAX_RECORD_ATTEMPTS = 5;
const SW_MAX_BATCH_SIZE = 100;
const SW_LOCAL_SYNC_FIELDS = ["sync_error", "sync_attempts", "sync_conflict"];

const SW_RECORD_ERROR_RULES = {
  not_assigned: { kind: "permanent", title: "No longer assigned to you", hint: "A dispatcher may have reassigned or removed this delivery, so your change can't be applied.", actions: ["discard"] },
  identity_mismatch: { kind: "permanent", title: "Saved under a different account", hint: "This change was recorded while signed in as someone else, so it can't be sent from this account.", actions: ["discard"] },
  not_found: { kind: "permanent", title: "Delivery no longer exists", hint: "The delivery was removed on the server.", actions: ["discard"] },
  invalid_agent: { kind: "permanent", title: "Agent not valid", hint: "The agent on this record doesn't exist in your organization.", actions: ["discard"] },
  invalid_record: { kind: "permanent", title: "The server rejected this update", hint: "Something in this update isn't valid, so retrying won't help.", actions: ["discard"] },
  base_version_required: { kind: "permanent", title: "Out of date", hint: "This update was saved before the delivery's version was known. Discard it and make the change again.", actions: ["discard"] },
  rejected_by_server: { kind: "permanent", title: "The server refused this update", hint: "The server turned this record down as invalid, so retrying won't help.", actions: ["discard"] },
  retry_exhausted: { kind: "permanent", title: "Couldn't sync after several tries", hint: "It failed repeatedly. Try again when you have a better connection, or ask your dispatcher.", actions: ["retry", "discard"] },
  concurrent_update: { kind: "retryable", title: "Changed by someone else at the same moment", hint: "It will be retried automatically.", actions: [] },
  server_error: { kind: "retryable", title: "The server had a problem with this update", hint: "It will be retried automatically.", actions: [] },
  pod_required: { kind: "retryable", waiting: true, title: "Waiting for proof of delivery", hint: "It will sync automatically once the proof of delivery has been uploaded.", actions: [] },
};
const SW_UNKNOWN_RULE = { kind: "retryable", title: "Couldn't sync this update", hint: "It will be retried automatically a few times.", actions: [] };

function swClassifyRecordError(serverError) {
  const code = (serverError && serverError.code) || "unknown_error";
  const rule = SW_RECORD_ERROR_RULES[code] || SW_UNKNOWN_RULE;
  const serverText = serverError && (serverError.error || serverError.message);
  return {
    code,
    kind: rule.kind,
    waiting: Boolean(rule.waiting),
    title: rule.title,
    message: typeof serverText === "string" && serverText ? serverText : rule.title,
    hint: rule.hint,
    actions: [...rule.actions],
  };
}

function swFormatServerDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (!item || !item.msg) return null;
        const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body" && p !== "records" && !Number.isInteger(p)).join(".") : "";
        return loc ? `${loc}: ${item.msg}` : item.msg;
      })
      .filter(Boolean);
    if (parts.length) return parts.slice(0, 3).join("; ");
  }
  return null;
}

function swExtractRecordFaults(detail, batchLength) {
  if (!Array.isArray(detail)) return [];
  const byIndex = new Map();
  for (const item of detail) {
    const loc = item && item.loc;
    if (!Array.isArray(loc) || loc[0] !== "body" || loc[1] !== "records" || !Number.isInteger(loc[2])) continue;
    if (loc[2] < 0 || loc[2] >= batchLength) continue;
    const field = loc.slice(3).join(".");
    const text = item.msg ? (field ? `${field}: ${item.msg}` : item.msg) : "invalid";
    byIndex.set(loc[2], byIndex.has(loc[2]) ? `${byIndex.get(loc[2])}; ${text}` : text);
  }
  return [...byIndex.entries()].map(([index, message]) => ({ index, message }));
}

// scope: "auth" (401/403 on /sync) | "rejected" (400/413/422: some record is at fault) |
//        "transport" (no response / transient status) | "server" (anything else: an outage, never blamed on a record)
function swClassifyRequestError(error) {
  const status = error && error.status;
  if (status === 401 || status === 403) return { scope: "auth", status };
  if (!Number.isInteger(status) || [408, 425, 429, 502, 503, 504].includes(status)) return { scope: "transport", status };
  if ([400, 413, 422].includes(status)) return { scope: "rejected", status };
  return { scope: "server", status };
}

// For the simple one-request-per-item queues (pings, chat, actions): "ok" | "auth" | "retry" | "permanent".
function swClassifyStatus(status) {
  if (status >= 200 && status < 300) return "ok";
  if (status === 401) return "auth"; // stale token: nothing in this queue can succeed until the app refreshes it
  if (status === 408 || status === 425 || status === 429 || status >= 500) return "retry";
  return "permanent"; // 400, 403, 404, 409, 410, 413, 422 ...: retrying the same request will not change the answer
}

function swChunk(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

function swWithoutLocalSyncFields(record) {
  const clean = { ...record };
  for (const field of SW_LOCAL_SYNC_FIELDS) delete clean[field];
  return clean;
}

function swDescribeConflict(conflict) {
  if (conflict.message) return `Order ${conflict.order_id}: ${conflict.message}`;
  const who = conflict.kept_by ? `${conflict.kept_by} already updated it` : "it was already updated";
  return `Order ${conflict.order_id}: your change to "${conflict.your_status}" was overridden — ${who} to "${conflict.kept_status}" more recently, so that's what was kept.`;
}

// --- Lost-update protection (mirrors src/services/syncRevision.js + syncOutcome.js) ---
// Each local edit bumps `local_revision`. We send the revision with each record and only
// write the server's answer back if the stored record is STILL at that revision; if the
// agent edited it again while the request was in flight, the newer edit stays "pending".

function swRevisionOf(record) {
  return Number.isInteger(record && record.local_revision) && record.local_revision >= 0 ? record.local_revision : 0;
}

function swGuardAgainstStaleAnswer(current, submittedRevision) {
  if (!current) return { write: false, reason: "deleted_locally" };
  if (current.sync_status !== "pending") return { write: false, reason: "already_settled" };
  if (swRevisionOf(current) !== submittedRevision) return { write: false, reason: "changed_during_sync" };
  return null;
}

function swDecideSyncedWrite(current, resolved, submittedRevision) {
  if (!current) return { write: false, reason: "deleted_locally" };
  if (current.sync_status !== "pending") return { write: false, reason: "already_synced" };
  if (swRevisionOf(current) !== submittedRevision) return { write: false, reason: "changed_during_sync" };
  return { write: true, record: { ...resolved, sync_status: "synced", local_revision: swRevisionOf(current) } };
}

function swDecideBlockedWrite(current, info, submittedRevision) {
  const stale = swGuardAgainstStaleAnswer(current, submittedRevision);
  if (stale) return stale;
  return { write: true, blocked: true, record: { ...current, sync_status: "blocked", sync_error: { ...info, at: new Date().toISOString() } } };
}

function swDecideRetryableFailureWrite(current, info, submittedRevision) {
  const stale = swGuardAgainstStaleAnswer(current, submittedRevision);
  if (stale) return stale;
  if (info.waiting) {
    return { write: true, blocked: false, record: { ...current, sync_error: { ...info, at: new Date().toISOString() } } };
  }
  const attempts = (Number.isInteger(current.sync_attempts) ? current.sync_attempts : 0) + 1;
  if (attempts >= SW_MAX_RECORD_ATTEMPTS) {
    const exhausted = swClassifyRecordError({ code: "retry_exhausted", message: `${info.message} (failed ${attempts} times)` });
    return {
      write: true, blocked: true,
      record: { ...current, sync_status: "blocked", sync_attempts: attempts, sync_error: { ...exhausted, at: new Date().toISOString() } },
    };
  }
  return { write: true, blocked: false, record: { ...current, sync_attempts: attempts, sync_error: { ...info, at: new Date().toISOString() } } };
}

function swDecideConflictWrite(current, resolved, conflict, submittedRevision) {
  const stale = swGuardAgainstStaleAnswer(current, submittedRevision);
  if (stale) return stale;
  return {
    write: true,
    record: {
      ...swWithoutLocalSyncFields(resolved),
      sync_status: "conflicted",
      local_revision: swRevisionOf(current),
      sync_conflict: {
        code: conflict.code || null,
        message: swDescribeConflict(conflict),
        your_status: conflict.your_status || null,
        kept_status: conflict.kept_status || null,
        at: new Date().toISOString(),
      },
    },
  };
}

// get + decide + put in ONE readwrite transaction: nothing can slip in between.
function swApplyDecision(db, id, decide) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction("deliveries", "readwrite");
    const store = tx.objectStore("deliveries");
    const getRequest = store.get(id);
    getRequest.onsuccess = () => {
      const decision = decide(getRequest.result);
      if (!decision.write) return resolve({ applied: false, reason: decision.reason });
      const putRequest = store.put(decision.record);
      putRequest.onsuccess = () => resolve({ applied: true, blocked: Boolean(decision.blocked), record: decision.record });
      putRequest.onerror = (event) => reject(event.target.error);
    };
    getRequest.onerror = (event) => reject(event.target.error);
  });
}

// --- What each background task reports back ---
// { synced, retry, blocked, authExpired }: how many items went through, how many still need ANOTHER
// attempt (the browser is asked to retry — see the 'sync' handler), how many were permanently
// refused (blocked/dropped; will never succeed by waiting), and whether the session expired.

function swNewReport() {
  return { synced: 0, retry: 0, blocked: 0, authExpired: false, failures: [] };
}

function swMergeReport(total, part) {
  if (!part) return;
  total.synced += part.synced || 0;
  total.retry += part.retry || 0;
  total.blocked += part.blocked || 0;
  if (part.authExpired) total.authExpired = true;
  if (part.failures) total.failures.push(...part.failures);
}

async function swReadDetail(response) {
  try {
    return (await response.json()).detail;
  } catch (error) {
    return undefined;
  }
}

async function swPostSync(context, records) {
  const response = await fetch(`${context.apiBaseUrl}/sync`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(context.token ? { Authorization: `Bearer ${context.token}` } : {}),
    },
    body: JSON.stringify({
      records,
      client_sent_at: new Date().toISOString(), // lets the server correct event times for a wrong device clock
    }),
  });
  if (!response.ok) {
    const error = new Error(`Background delivery sync failed (HTTP ${response.status})`);
    error.status = response.status;
    error.detail = await swReadDetail(response);
    throw error;
  }
  return response.json();
}

async function swBlockRecord(db, record, info, acc) {
  const result = await swApplyDecision(db, record.id, (current) => swDecideBlockedWrite(current, info, swRevisionOf(record)));
  if (result.applied) acc.blocked += 1;
  else if (result.reason === "changed_during_sync") acc.deferred += 1;
}

// The server refused the whole request as invalid (400/413/422): find the bad record(s) and block only those.
async function swIsolateRejected(db, context, batch, error, acc) {
  if (batch.length === 1) {
    const message = swFormatServerDetail(error.detail) || `The server refused this record (HTTP ${error.status}).`;
    await swBlockRecord(db, batch[0], swClassifyRecordError({ code: "rejected_by_server", message }), acc);
    return;
  }
  const faults = swExtractRecordFaults(error.detail, batch.length);
  if (faults.length > 0 && faults.length < batch.length) {
    const bad = new Set(faults.map((f) => f.index));
    for (const fault of faults) {
      await swBlockRecord(db, batch[fault.index], swClassifyRecordError({ code: "invalid_record", message: fault.message }), acc);
    }
    await swSubmitBatch(db, context, batch.filter((_, index) => !bad.has(index)), acc);
    return;
  }
  const middle = Math.ceil(batch.length / 2);
  await swSubmitBatch(db, context, batch.slice(0, middle), acc);
  await swSubmitBatch(db, context, batch.slice(middle), acc);
}

async function swSubmitBatch(db, context, batch, acc) {
  if (batch.length === 0) return;
  const submitted = new Map(batch.map((r) => [r.id, swRevisionOf(r)]));
  // /sync requires the agent's access token. The context token is the one written when the app last
  // ran; if it has expired (30 min lifetime) the server answers 401, this throws, and the records
  // stay pending — the foreground app (which refreshes tokens) syncs them as soon as it's opened.
  let result;
  try {
    result = await swPostSync(
      context,
      batch.map((r) => ({
        ...swWithoutLocalSyncFields(r),
        local_revision: swRevisionOf(r),
        base_version: Number.isInteger(r.version) ? r.version : 0, // the server version this record was read at (0 = unknown -> treated as oldest possible)
      }))
    );
  } catch (error) {
    if (swClassifyRequestError(error).scope === "rejected") {
      await swIsolateRejected(db, context, batch, error, acc);
      return;
    }
    throw error;
  }

  const byId = new Map(batch.map((r) => [r.id, r]));
  const acknowledged = new Map((result.acknowledged || []).map((a) => [a.id, a.local_revision]));
  const conflictsById = new Map((result.conflicts || []).filter((c) => c && c.id).map((c) => [c.id, c]));

  for (const resolved of result.resolved_records || []) {
    const revision = acknowledged.has(resolved.id) ? acknowledged.get(resolved.id) : submitted.get(resolved.id);
    const conflict = conflictsById.get(resolved.id);
    const outcome = conflict
      ? await swApplyDecision(db, resolved.id, (current) => swDecideConflictWrite(current, resolved, conflict, revision))
      : await swApplyDecision(db, resolved.id, (current) => swDecideSyncedWrite(current, resolved, revision));
    if (outcome.applied) acc.count += 1;
    else if (outcome.reason === "changed_during_sync") acc.deferred += 1;
  }

  for (const serverError of result.errors || []) {
    const record = byId.get(serverError.id);
    if (!record) continue;
    const revision = submitted.get(record.id);
    const info = swClassifyRecordError(serverError);
    if (info.kind === "permanent") {
      await swBlockRecord(db, record, info, acc);
      continue;
    }
    const outcome = await swApplyDecision(db, record.id, (current) => swDecideRetryableFailureWrite(current, info, revision));
    if (outcome.applied && outcome.blocked) acc.blocked += 1;
    else if (outcome.applied) acc.retrying += 1;
    else if (outcome.reason === "changed_during_sync") acc.deferred += 1;
  }
}

async function swSyncDeliveriesOnePass(db, context, acc) {
  // Re-read the CURRENT queue on every pass (never reuse an old snapshot). Only "pending" records:
  // blocked and conflicted ones are never re-sent.
  const all = await swGetAll(db, "deliveries");
  const pending = all.filter((r) => r.sync_status === "pending");
  acc.deferred = 0;
  for (const batch of swChunk(pending, SW_MAX_BATCH_SIZE)) {
    await swSubmitBatch(db, context, batch, acc);
  }
}

async function backgroundSyncDeliveries(context) {
  const dbName = `delivery_sync_db_${context.userId}`;
  const report = swNewReport();

  const run = async () => {
    const db = await swOpenExistingDb(dbName, 2);
    if (!db) return report; // this account has no delivery queue: nothing to do
    const acc = { count: 0, deferred: 0, blocked: 0, retrying: 0 };
    try {
      // A few extra passes if the agent kept editing while we uploaded (bounded).
      for (let pass = 0; pass < 4; pass += 1) {
        await swSyncDeliveriesOnePass(db, context, acc);
        if (!acc.deferred) break;
      }
    } catch (error) {
      // A failed REQUEST. Auth: the session needs the foreground app, retrying here can't fix it.
      // Anything else (network, server down): report it so the browser retries this sync later.
      if (swClassifyRequestError(error).scope === "auth") report.authExpired = true;
      else {
        report.retry += 1;
        report.failures.push(`deliveries: ${error.message}`);
      }
    }
    report.synced += acc.count;
    report.blocked += acc.blocked;
    report.retry += acc.retrying;
    return report;
  };

  // Same named lock the page takes (see getSyncLockName in src/services/indexedDb.js): if a tab
  // is already syncing this user's queue, skip — it will upload these records itself.
  if (self.navigator && self.navigator.locks && self.navigator.locks.request) {
    return self.navigator.locks.request(`delivery-sync:${dbName}`, { ifAvailable: true }, (lock) => (lock ? run() : report));
  }
  return run();
}

async function backgroundSyncLocationPings(context) {
  const report = swNewReport();
  const db = await swOpenExistingDb(`delivery_sync_db_${context.userId}`, 2);
  if (!db) return report;
  const pings = (await swGetAll(db, "location_pings")).sort((a, b) => a.id - b.id);

  for (const ping of pings) {
    let response;
    try {
      response = await fetch(`${context.apiBaseUrl}/users/me/location`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${context.token}` },
        body: JSON.stringify({ latitude: ping.latitude, longitude: ping.longitude }),
      });
    } catch (error) {
      report.retry += 1; // offline: keep the rest queued, ask for another attempt
      break;
    }
    const kind = swClassifyStatus(response.status);
    if (kind === "ok") {
      await swDelete(db, "location_pings", ping.id);
      report.synced += 1;
    } else if (kind === "auth") {
      report.authExpired = true;
      break;
    } else if (kind === "retry") {
      report.retry += 1;
      break; // server busy/down: stop, retry the rest later
    } else {
      // A ping the server will never accept must not block every ping behind it, forever.
      await swDelete(db, "location_pings", ping.id);
      report.blocked += 1;
    }
  }
  return report;
}

async function backgroundSyncChatMessages(context) {
  const report = swNewReport();
  const db = await swOpenExistingDb(`chat_queue_${context.userId}`, 1);
  if (!db) return report;
  const messages = (await swGetAll(db, "pending_messages")).sort((a, b) => a.id - b.id);

  for (const msg of messages) {
    let response;
    try {
      response = await fetch(`${context.apiBaseUrl}/deliveries/${msg.delivery_id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${context.token}` },
        body: JSON.stringify({ message: msg.message }),
      });
    } catch (error) {
      report.retry += 1;
      break;
    }
    const kind = swClassifyStatus(response.status);
    if (kind === "ok") {
      await swDelete(db, "pending_messages", msg.id);
      report.synced += 1;
    } else if (kind === "auth") {
      // Stale token: nothing else in this queue will succeed either.
      report.authExpired = true;
      break;
    } else if (kind === "retry") {
      // A transient error on this one message shouldn't block ones after it — keep it, move on, ask for a retry.
      report.retry += 1;
    } else {
      // Refused for good (e.g. the delivery is no longer yours): it would otherwise be re-sent on every sync forever.
      await swDelete(db, "pending_messages", msg.id);
      report.blocked += 1;
    }
  }
  return report;
}

async function backgroundSyncCart(context) {
  const report = swNewReport();
  const db = await swOpenExistingDb(`customer_cache_${context.userId}`, 2);
  if (!db) return report;
  const authHeaders = { "Content-Type": "application/json", Authorization: `Bearer ${context.token}` };

  // Sends one request; returns "ok" | "permanent", or "stop" after recording why the whole task should stop.
  const send = async (url, init) => {
    let response;
    try {
      response = await fetch(url, init);
    } catch (error) {
      report.retry += 1;
      return { kind: "stop" };
    }
    const kind = swClassifyStatus(response.status);
    if (kind === "auth") { report.authExpired = true; return { kind: "stop" }; }
    if (kind === "retry") { report.retry += 1; return { kind: "stop" }; }
    return { kind, response };
  };

  // 1. Push the local-first cart to the server if it's been changed
  // since the last successful sync (mirrors cartSyncEngine.js's
  // pushCartToServer: clear the server cart, then re-add each local
  // line — correct because the local cart is the source of truth).
  const metaRows = await swGetAll(db, "meta");
  const dirtyRow = metaRows.find((r) => r.key === "cart_dirty");
  if (dirtyRow && dirtyRow.value) {
    const localItems = await swGetAll(db, "local_cart");
    let pushed = (await send(`${context.apiBaseUrl}/customer/cart/`, { method: "DELETE", headers: authHeaders })).kind !== "stop";
    for (const item of localItems) {
      if (!pushed) break;
      // A line the server refuses for good (e.g. a product that no longer exists) must not stop the others.
      const outcome = await send(`${context.apiBaseUrl}/customer/cart/`, {
        method: "POST", headers: authHeaders, body: JSON.stringify({ product_id: item.product_id, quantity: item.quantity }),
      });
      if (outcome.kind === "stop") pushed = false;
    }
    // Only mark the cart "clean" if every request actually went through; otherwise it stays dirty and is retried.
    if (pushed) {
      await swPut(db, "meta", { key: "cart_dirty", value: false });
      report.synced += 1;
    }
  }

  // 2. Complete a checkout that was queued while offline — but ONLY
  // when it's test-mode (no real payment gateway to open a widget for).
  // A real-gateway checkout needs a foreground tab and a user gesture to
  // open Razorpay's Checkout.js, which a background sync firing with
  // every tab closed genuinely cannot do — so it's deliberately left
  // queued for Storefront.jsx to prompt "Complete Payment Now" the next
  // time the app is opened, exactly like cartSyncEngine.js's
  // processPendingCheckout already handles this same case.
  const pendingRow = metaRows.find((r) => r.key === "pending_checkout");
  if (pendingRow && pendingRow.value && !report.authExpired && report.retry === 0) {
    const details = pendingRow.value;
    const outcome = await send(`${context.apiBaseUrl}/customer/checkout`, {
      method: "POST", headers: authHeaders,
      body: JSON.stringify({ address_line: details.address_line, city: details.city, phone: details.phone }),
    });
    if (outcome.kind === "ok") {
      const checkoutResp = await outcome.response.json().catch(() => ({}));
      if (checkoutResp.is_test_mode) {
        const verified = await send(`${context.apiBaseUrl}/customer/checkout/verify`, {
          method: "POST", headers: authHeaders, body: JSON.stringify({ order_id: checkoutResp.order_id }),
        });
        if (verified.kind !== "stop") {
          await swDelete(db, "meta", "pending_checkout");
          const cartItems = await swGetAll(db, "local_cart");
          for (const item of cartItems) await swDelete(db, "local_cart", item.product_id);
          report.synced += 1;
        }
      }
      // else: real gateway, checkout order created server-side but left unpaid — pending_checkout
      // stays queued so the app can prompt for payment next time it's opened.
    }
    // "permanent": the server refuses this checkout (e.g. out of stock). It stays queued for the foreground
    // app, which can show the real error and let the customer fix it; retrying silently here can't help.
  }

  return report;
}

async function backgroundSyncCustomerActions(context) {
  const report = swNewReport();
  const db = await swOpenExistingDb(`customer_cache_${context.userId}`, 2);
  if (!db) return report;
  const actions = (await swGetAll(db, "pending_actions")).sort((a, b) => (a.queued_at > b.queued_at ? 1 : -1));
  const authHeaders = { Authorization: `Bearer ${context.token}` };

  for (const action of actions) {
    const endpoint =
      action.type === "cancel"
        ? `/customer/deliveries/${action.delivery_id}/cancel`
        : `/customer/deliveries/${action.delivery_id}/reorder`;
    let response;
    try {
      response = await fetch(`${context.apiBaseUrl}${endpoint}`, { method: "POST", headers: authHeaders });
    } catch (error) {
      report.retry += 1; // network error — still offline, stop here and retry the rest next time
      break;
    }
    const kind = swClassifyStatus(response.status);
    if (kind === "ok") {
      await swDelete(db, "pending_actions", action.id);
      report.synced += 1;
    } else if (kind === "auth") {
      report.authExpired = true;
      break;
    } else if (kind === "retry") {
      // Before, ANY non-OK response (even a 503 or 429) deleted the action — silently losing it.
      // A transient failure must keep it queued.
      report.retry += 1;
      break;
    } else {
      // A real rejection (e.g. order no longer eligible) — won't succeed on retry, drop it
      // rather than queue forever, but report it instead of hiding it.
      await swDelete(db, "pending_actions", action.id);
      report.blocked += 1;
    }
  }
  return report;
}

async function backgroundSyncDispatcherActions(context) {
  const report = swNewReport();
  const db = await swOpenExistingDb(`dispatcher_cache_${context.userId}`, 2);
  if (!db) return report;
  const actions = (await swGetAll(db, "pending_actions")).sort((a, b) => a.id - b.id);
  const authHeaders = { "Content-Type": "application/json", Authorization: `Bearer ${context.token}` };

  for (const action of actions) {
    let response;
    try {
      if (action.type === "create_delivery") {
        response = await fetch(`${context.apiBaseUrl}/deliveries/`, { method: "POST", headers: authHeaders, body: JSON.stringify(action.payload) });
      } else if (action.type === "assign_agent") {
        response = await fetch(`${context.apiBaseUrl}/deliveries/${action.payload.delivery_id}/assign-agent`, {
          method: "PATCH", headers: authHeaders, body: JSON.stringify({ agent_id: action.payload.agent_id }),
        });
      } else if (action.type === "create_product") {
        response = await fetch(`${context.apiBaseUrl}/admin/products/`, { method: "POST", headers: authHeaders, body: JSON.stringify(action.payload) });
      } else if (action.type === "update_product") {
        response = await fetch(`${context.apiBaseUrl}/admin/products/${action.payload.product_id}`, {
          method: "PATCH", headers: authHeaders, body: JSON.stringify(action.payload.updates),
        });
      } else if (action.type === "delete_product") {
        response = await fetch(`${context.apiBaseUrl}/admin/products/${action.payload.product_id}`, { method: "DELETE", headers: authHeaders });
      } else {
        await swDelete(db, "pending_actions", action.id); // unrecognized type — drop rather than get stuck on it forever
        report.blocked += 1;
        continue;
      }
    } catch (error) {
      report.retry += 1; // network error — still offline, stop and retry the rest next time
      break;
    }

    const kind = swClassifyStatus(response.status);
    if (kind === "ok") {
      await swDelete(db, "pending_actions", action.id);
      report.synced += 1;
    } else if (kind === "auth") {
      report.authExpired = true;
      break;
    } else if (kind === "retry") {
      report.retry += 1; // transient: keep it (and everything after it, to preserve order) queued
      break;
    } else {
      // A real rejection — won't succeed on retry, drop it (and report it). Later actions may depend on this one
      // (e.g. assigning an agent to a delivery that failed to create), so stop here rather than attempt the rest out of order.
      await swDelete(db, "pending_actions", action.id);
      report.blocked += 1;
      break;
    }
  }
  return report;
}

const SW_BACKGROUND_TASKS = [
  ["deliveries", backgroundSyncDeliveries],
  ["location pings", backgroundSyncLocationPings],
  ["chat messages", backgroundSyncChatMessages],
  ["cart", backgroundSyncCart],
  ["customer actions", backgroundSyncCustomerActions],
  ["dispatcher actions", backgroundSyncDispatcherActions],
];

async function runBackgroundSync() {
  const total = swNewReport();
  const context = await swGetSyncContext();
  if (!context || !context.userId || !context.apiBaseUrl) return total;

  // Each piece is independent — one failing must not stop the others from being attempted.
  const results = await Promise.allSettled(SW_BACKGROUND_TASKS.map(([, task]) => task(context)));

  // The results are INSPECTED, never discarded: a task that threw (e.g. IndexedDB error) or reported a
  // retryable failure counts toward `retry`, so the sync event can ask the browser to try again.
  results.forEach((result, index) => {
    const name = SW_BACKGROUND_TASKS[index][0];
    if (result.status === "fulfilled") {
      swMergeReport(total, result.value);
    } else {
      total.retry += 1;
      total.failures.push(`${name}: ${(result.reason && result.reason.message) || result.reason}`);
    }
  });

  if (total.synced > 0 || total.blocked > 0 || total.retry > 0 || total.authExpired) {
    const clients = await self.clients.matchAll({ type: "window" });
    for (const client of clients) {
      client.postMessage({
        type: "background-sync-complete",
        syncedCount: total.synced,
        blockedCount: total.blocked,
        retryCount: total.retry,
        authExpired: total.authExpired,
      });
    }
  }
  return total;
}

const SW_SYNC_TAG = "delivery-sync-background-sync";

self.addEventListener("sync", (event) => {
  if (event.tag !== SW_SYNC_TAG) return;
  event.waitUntil(
    runBackgroundSync().then(async (summary) => {
      if (summary.retry === 0) return; // everything that could be sent was settled (or needs the foreground app to sign in)
      // Something retryable failed. Rejecting the promise is how a sync handler asks the browser to
      // fire it again later (with backoff); resolving would tell the browser the work is DONE.
      if (event.lastChance) {
        // The browser will not retry again after this attempt. Register again so the work isn't orphaned.
        try {
          await self.registration.sync.register(SW_SYNC_TAG);
        } catch (error) {
          // ignore: the foreground app's timer and "online" handler still pick the records up
        }
        return;
      }
      throw new Error(`Background sync incomplete: ${summary.retry} operation(s) need a retry (${summary.failures.join("; ") || "server or network unavailable"})`);
    })
  );
});
