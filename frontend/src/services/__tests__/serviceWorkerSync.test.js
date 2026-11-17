/**
 * Tests for the service worker's background sync (public/sw.js).
 *
 * The worker is a plain script (it can't be imported), so it is loaded into a sandbox (node:vm) with:
 *   - a tiny in-memory IndexedDB (just the calls the worker makes),
 *   - a scripted fetch,
 *   - a fake `self` that records the 'sync' listener, window messages and re-registrations.
 * Then real 'sync' events are fired and we check what the BROWSER would see: a rejected waitUntil promise
 * means "try this sync again later"; a resolved one means "done".
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, it, expect, vi, beforeEach } from "vitest";

const SW_SOURCE = fs.readFileSync(path.resolve(process.cwd(), "public/sw.js"), "utf8");
const USER = "u1";
const API = "https://api.test";

// ---------- minimal in-memory IndexedDB ----------
function makeFakeIndexedDB(spec) {
  // spec: { dbName: { storeName: { keyPath, records: [] } } }
  const dbs = {};
  for (const [dbName, stores] of Object.entries(spec)) {
    dbs[dbName] = {};
    for (const [storeName, { keyPath, records }] of Object.entries(stores)) {
      dbs[dbName][storeName] = { keyPath, map: new Map(records.map((r) => [r[keyPath], structuredClone(r)])) };
    }
  }
  const created = [];
  const failingStores = new Set();

  const async_ = (fn) => queueMicrotask(fn);

  function makeDb(dbName) {
    const stores = dbs[dbName];
    return {
      objectStoreNames: { contains: (name) => Boolean(stores[name]) },
      transaction(storeName) {
        if (failingStores.has(storeName)) throw new Error(`simulated IndexedDB failure on ${storeName}`);
        const store = stores[storeName];
        const tx = { oncomplete: null, onerror: null };
        const scheduleComplete = () => async_(() => tx.oncomplete && tx.oncomplete());
        const req = (compute, isWrite) => {
          const request = {};
          async_(() => {
            request.result = compute();
            if (request.onsuccess) request.onsuccess({ target: request });
            if (isWrite) scheduleComplete();
          });
          return request;
        };
        tx.objectStore = () => ({
          get: (key) => req(() => (store.map.has(key) ? structuredClone(store.map.get(key)) : undefined), false),
          getAll: () => req(() => [...store.map.values()].map((r) => structuredClone(r)), false),
          put: (value) => req(() => { store.map.set(value[store.keyPath], structuredClone(value)); return value[store.keyPath]; }, true),
          delete: (key) => req(() => { store.map.delete(key); }, true),
        });
        return tx;
      },
    };
  }

  return {
    open(name) {
      const request = {};
      async_(() => {
        if (!dbs[name]) {
          // The worker must NOT create databases: it aborts the upgrade (see swOpenExistingDb).
          created.push(name);
          let aborted = false;
          request.transaction = { abort: () => { aborted = true; } };
          if (request.onupgradeneeded) request.onupgradeneeded({ target: request });
          async_(() => request.onerror && request.onerror({ target: { error: new Error("aborted") }, preventDefault() {} }));
          return aborted;
        }
        request.result = makeDb(name);
        request.onsuccess && request.onsuccess({ target: request });
      });
      return request;
    },
    _dbs: dbs,
    _created: created,
    _failStore: (name) => failingStores.add(name),
    rows: (dbName, storeName) => [...dbs[dbName][storeName].map.values()],
  };
}

const json = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

function loadWorker({ deliveries = [], extraDbs = {}, fetchImpl, token = "tok" } = {}) {
  const idb = makeFakeIndexedDB({
    app_sync_context: { context: { keyPath: "key", records: [{ key: "current", userId: USER, apiBaseUrl: API, token }] } },
    [`delivery_sync_db_${USER}`]: {
      deliveries: { keyPath: "id", records: deliveries },
      location_pings: { keyPath: "id", records: extraDbs.pings || [] },
    },
    ...(extraDbs.dbs || {}),
  });

  const handlers = {};
  const messages = [];
  const registered = [];
  const fetchMock = vi.fn(fetchImpl || (async () => json(200, {})));
  const self = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    clients: { matchAll: async () => [{ postMessage: (m) => messages.push(m) }] },
    registration: { sync: { register: async (tag) => { registered.push(tag); } } },
    navigator: {},
  };
  const sandbox = { self, indexedDB: idb, fetch: fetchMock, console, Promise, Map, Set, Date, JSON, Math, Number, Array, Object, Error, String, structuredClone, setTimeout };
  vm.createContext(sandbox);
  vm.runInContext(SW_SOURCE, sandbox);

  const fire = (props = {}) => {
    let promise;
    handlers.sync({ tag: "delivery-sync-background-sync", lastChance: false, waitUntil: (p) => { promise = p; }, ...props });
    return promise;
  };
  const delivery = (id) => idb.rows(`delivery_sync_db_${USER}`, "deliveries").find((r) => r.id === id);
  return { fire, idb, fetchMock, messages, registered, delivery, sandbox };
}

const pendingRecord = (id, extra = {}) => ({ id, order_id: `ORD-${id}`, status: "delivered", version: 1, local_revision: 1, sync_status: "pending", ...extra });
const syncCalls = (fetchMock) => fetchMock.mock.calls.filter(([url]) => url.endsWith("/sync"));

describe("service worker background sync — deliveries", () => {
  beforeEach(() => vi.clearAllMocks());

  it("blocks a record the server refuses permanently, finishes without asking for a retry, and never re-sends it", async () => {
    const w = loadWorker({
      deliveries: [pendingRecord("1")],
      fetchImpl: async () => json(200, { resolved_records: [], conflicts: [], errors: [{ id: "1", error: "This delivery isn't assigned to you.", code: "not_assigned" }] }),
    });

    await expect(w.fire()).resolves.toBeUndefined(); // resolved = done; a permanent refusal is not retried by the browser
    expect(w.delivery("1")).toMatchObject({ sync_status: "blocked", sync_error: { code: "not_assigned", kind: "permanent", message: "This delivery isn't assigned to you." } });
    expect(syncCalls(w.fetchMock)).toHaveLength(1);

    await w.fire(); // the next background sync must not touch it again
    expect(syncCalls(w.fetchMock)).toHaveLength(1);
  });

  it("asks the browser to retry (rejects) when the network is down, and keeps the record pending", async () => {
    const w = loadWorker({ deliveries: [pendingRecord("1")], fetchImpl: async () => { throw new TypeError("Failed to fetch"); } });
    await expect(w.fire()).rejects.toThrow("Background sync incomplete");
    expect(w.delivery("1").sync_status).toBe("pending");
  });

  it("asks the browser to retry when the server answers 503", async () => {
    const w = loadWorker({ deliveries: [pendingRecord("1")], fetchImpl: async () => json(503, {}) });
    await expect(w.fire()).rejects.toThrow("Background sync incomplete");
    expect(w.delivery("1").sync_status).toBe("pending");
  });

  it("does not retry on an expired session (only the foreground app can fix that) but tells open tabs", async () => {
    const w = loadWorker({ deliveries: [pendingRecord("1")], fetchImpl: async () => json(401, {}) });
    await expect(w.fire()).resolves.toBeUndefined();
    expect(w.delivery("1").sync_status).toBe("pending");
    expect(w.messages).toEqual([expect.objectContaining({ type: "background-sync-complete", authExpired: true })]);
  });

  it("on the browser's LAST chance it re-registers the sync instead of letting the work be orphaned", async () => {
    const w = loadWorker({ deliveries: [pendingRecord("1")], fetchImpl: async () => json(503, {}) });
    await expect(w.fire({ lastChance: true })).resolves.toBeUndefined();
    expect(w.registered).toEqual(["delivery-sync-background-sync"]);
  });

  it("a 422 naming one record blocks only that one; the others still sync", async () => {
    const w = loadWorker({
      deliveries: [pendingRecord("1"), pendingRecord("2"), pendingRecord("3")],
      fetchImpl: async (url, init) => {
        const sent = JSON.parse(init.body).records;
        if (sent.some((r) => r.id === "2")) {
          return json(422, { detail: [{ loc: ["body", "records", sent.findIndex((r) => r.id === "2"), "status"], msg: "Input should be a valid status" }] });
        }
        return json(200, { resolved_records: sent.map((r) => ({ id: r.id, status: "delivered", version: 2 })), errors: [], conflicts: [] });
      },
    });

    await expect(w.fire()).resolves.toBeUndefined();
    expect(w.delivery("2")).toMatchObject({ sync_status: "blocked", sync_error: { code: "invalid_record", message: "status: Input should be a valid status" } });
    expect(w.delivery("1").sync_status).toBe("synced");
    expect(w.delivery("3").sync_status).toBe("synced");
  });

  it("stores a change the server did not apply as 'conflicted', with the server's copy and the explanation", async () => {
    const w = loadWorker({
      deliveries: [pendingRecord("1")],
      fetchImpl: async () => json(200, {
        resolved_records: [{ id: "1", order_id: "ORD-1", status: "cancelled", version: 5 }],
        errors: [],
        conflicts: [{ id: "1", order_id: "ORD-1", code: "stale_version", message: "Cancelled after you last synced.", your_status: "delivered", kept_status: "cancelled" }],
      }),
    });
    await w.fire();
    expect(w.delivery("1")).toMatchObject({ sync_status: "conflicted", status: "cancelled", version: 5, sync_conflict: { message: "Order ORD-1: Cancelled after you last synced." } });
  });

  it("counts retryable record errors and blocks the record after 5 failed attempts", async () => {
    const w = loadWorker({
      deliveries: [pendingRecord("1")],
      fetchImpl: async () => json(200, { resolved_records: [], conflicts: [], errors: [{ id: "1", error: "busy", code: "concurrent_update" }] }),
    });
    for (let attempt = 1; attempt <= 4; attempt += 1) {
      await expect(w.fire()).rejects.toThrow("Background sync incomplete"); // still retryable: browser is asked to try again
      expect(w.delivery("1")).toMatchObject({ sync_status: "pending", sync_attempts: attempt });
    }
    await expect(w.fire()).resolves.toBeUndefined(); // 5th failure: blocked, no more retries
    expect(w.delivery("1")).toMatchObject({ sync_status: "blocked", sync_error: { code: "retry_exhausted" } });
  });

  it("a delivery waiting for proof of delivery stays pending with its explanation and is NEVER blocked, however many tries", async () => {
    const w = loadWorker({
      deliveries: [pendingRecord("1")],
      fetchImpl: async () => json(200, { resolved_records: [], conflicts: [], errors: [{ id: "1", error: "Proof of delivery is required.", code: "pod_required" }] }),
    });
    for (let i = 0; i < 8; i += 1) await w.fire().catch(() => {});
    const record = w.delivery("1");
    expect(record.sync_status).toBe("pending");
    expect(record.sync_attempts).toBeUndefined();
    expect(record.sync_error).toMatchObject({ code: "pod_required", waiting: true });
  });

  it("never overwrites an edit made while the request was in flight: the newer edit is uploaded in a follow-up pass", async () => {
    let w;
    let call = 0;
    w = loadWorker({
      deliveries: [pendingRecord("1")],
      fetchImpl: async (url, init) => {
        call += 1;
        const sent = JSON.parse(init.body).records[0];
        if (call === 1) {
          // the agent edits the record while the first upload is in flight
          const rows = w.idb._dbs[`delivery_sync_db_${USER}`].deliveries.map;
          rows.set("1", { ...rows.get("1"), local_revision: 2, status: "failed" });
          return json(200, { resolved_records: [{ id: "1", status: "delivered", version: 2 }], errors: [], conflicts: [] });
        }
        return json(200, { resolved_records: [{ id: "1", status: sent.status, version: 3 }], errors: [], conflicts: [] });
      },
    });

    await expect(w.fire()).resolves.toBeUndefined();

    const calls = syncCalls(w.fetchMock);
    expect(calls).toHaveLength(2);
    expect(JSON.parse(calls[1][1].body).records[0]).toMatchObject({ local_revision: 2, status: "failed" }); // the NEWER edit was sent
    expect(w.delivery("1")).toMatchObject({ sync_status: "synced", status: "failed", version: 3 }); // and the stale answer never replaced it
  });

  it("does nothing, and creates no databases, for an account that has no queues", async () => {
    const w = loadWorker({});
    // remove the delivery db so this account has none
    delete w.idb._dbs[`delivery_sync_db_${USER}`];
    await expect(w.fire()).resolves.toBeUndefined();
    expect(w.fetchMock).not.toHaveBeenCalled();
    expect(w.idb._created.length).toBeGreaterThan(0); // it tried to open them...
    expect(Object.keys(w.idb._dbs)).toEqual(["app_sync_context"]); // ...but never created any
  });
});

describe("service worker background sync — other queues", () => {
  beforeEach(() => vi.clearAllMocks());

  it("a queue task that THROWS is not swallowed: the sync event rejects so the browser retries", async () => {
    const w = loadWorker({ deliveries: [] });
    w.idb._failStore("location_pings");
    await expect(w.fire()).rejects.toThrow("location pings: simulated IndexedDB failure");
  });

  it("a location ping the server will never accept is dropped, so it can't block the pings behind it", async () => {
    const w = loadWorker({
      extraDbs: { pings: [{ id: 1, latitude: 999, longitude: 0 }, { id: 2, latitude: 12.9, longitude: 77.5 }] },
      fetchImpl: async (url, init) => (JSON.parse(init.body).latitude === 999 ? json(422, {}) : json(200, {})),
    });
    await expect(w.fire()).resolves.toBeUndefined();
    expect(w.idb.rows(`delivery_sync_db_${USER}`, "location_pings")).toEqual([]);
    expect(w.messages[0]).toMatchObject({ syncedCount: 1, blockedCount: 1 });
  });

  it("keeps location pings queued and asks for a retry while the server is unavailable", async () => {
    const w = loadWorker({ extraDbs: { pings: [{ id: 1, latitude: 1, longitude: 1 }] }, fetchImpl: async () => json(500, {}) });
    await expect(w.fire()).rejects.toThrow("Background sync incomplete");
    expect(w.idb.rows(`delivery_sync_db_${USER}`, "location_pings")).toHaveLength(1);
  });

  const customerDb = (actions) => ({
    dbs: { [`customer_cache_${USER}`]: {
      pending_actions: { keyPath: "id", records: actions },
      meta: { keyPath: "key", records: [] },
      local_cart: { keyPath: "product_id", records: [] },
    } },
  });

  it("a queued customer action survives a transient 503 (it used to be deleted on ANY non-OK response)", async () => {
    const w = loadWorker({ extraDbs: customerDb([{ id: 1, type: "cancel", delivery_id: "d1", queued_at: "2026-01-01" }]), fetchImpl: async () => json(503, {}) });
    await expect(w.fire()).rejects.toThrow("Background sync incomplete");
    expect(w.idb.rows(`customer_cache_${USER}`, "pending_actions")).toHaveLength(1);
  });

  it("a customer action the server refuses for good is dropped and reported, not retried forever", async () => {
    const w = loadWorker({ extraDbs: customerDb([{ id: 1, type: "cancel", delivery_id: "d1", queued_at: "2026-01-01" }]), fetchImpl: async () => json(409, {}) });
    await expect(w.fire()).resolves.toBeUndefined();
    expect(w.idb.rows(`customer_cache_${USER}`, "pending_actions")).toHaveLength(0);
    expect(w.messages[0]).toMatchObject({ blockedCount: 1 });
  });

  it("the cart stays 'dirty' (to be retried) when pushing it to the server fails", async () => {
    const dbs = { [`customer_cache_${USER}`]: {
      pending_actions: { keyPath: "id", records: [] },
      meta: { keyPath: "key", records: [{ key: "cart_dirty", value: true }] },
      local_cart: { keyPath: "product_id", records: [{ product_id: "p1", quantity: 2 }] },
    } };
    const w = loadWorker({ extraDbs: { dbs }, fetchImpl: async () => json(503, {}) });
    await expect(w.fire()).rejects.toThrow("Background sync incomplete");
    expect(w.idb.rows(`customer_cache_${USER}`, "meta").find((r) => r.key === "cart_dirty").value).toBe(true);
  });
});
