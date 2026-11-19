/**
 * Mirrors frontend/src/services/__tests__/syncEngine.test.js's
 * approach closely (mock the storage layer + the network call, drive
 * retries with real fake timers) — same underlying logic being
 * tested, ported to this file's actual dependencies (expo-network's
 * connectivity check and React Native's AppState in place of
 * navigator.onLine and the browser's "online" event).
 */

jest.mock("../offlineStore", () => ({
  getPendingDeliveries: jest.fn(),
  getCachedDeliveries: jest.fn(),
  markAsSyncedIfUnchanged: jest.fn(),
  markBlockedIfUnchanged: jest.fn(),
  markRetryableFailureIfUnchanged: jest.fn(),
  markConflictedIfUnchanged: jest.fn(),
  retryBlockedRecord: jest.fn(),
  acknowledgeConflict: jest.fn(),
  discardLocalRecord: jest.fn(),
}));
jest.mock("expo-network", () => ({
  getNetworkStateAsync: jest.fn(),
}));
jest.mock("../api", () => ({
  API_BASE_URL: "http://test-server",
  getAccessToken: jest.fn(),
  refreshAccessToken: jest.fn(),
}));

import { AppState } from "react-native";
import * as Network from "expo-network";
import {
  getPendingDeliveries, getCachedDeliveries, markAsSyncedIfUnchanged, markBlockedIfUnchanged,
  markRetryableFailureIfUnchanged, markConflictedIfUnchanged, retryBlockedRecord, discardLocalRecord,
} from "../offlineStore";
import { getAccessToken, refreshAccessToken } from "../api";
import { describeConflict, runSync, startAutoSync, getSyncSummary, retryBlocked, discardBlocked } from "../offlineSync";

describe("describeConflict", () => {
  it("names who overrode the change when kept_by is present", () => {
    const message = describeConflict({
      order_id: "ORD-1",
      your_status: "picked_up",
      kept_status: "delivered",
      kept_by: "Rahul K.",
    });
    expect(message).toBe(
      'Order ORD-1: your change to "picked_up" was overridden — Rahul K. already updated it to "delivered" more recently, so that\'s what was kept.'
    );
  });

  it("uses the server's own explanation when it gives one", () => {
    expect(describeConflict({ order_id: "ORD-3", message: "This delivery was cancelled after you last synced." }))
      .toBe("Order ORD-3: This delivery was cancelled after you last synced.");
  });

  it("falls back to a generic phrase when kept_by is missing", () => {
    const message = describeConflict({
      order_id: "ORD-2",
      your_status: "out_for_delivery",
      kept_status: "delivered",
      kept_by: null,
    });
    expect(message).toContain("it was already updated");
  });
});

describe("runSync", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    markAsSyncedIfUnchanged.mockResolvedValue({ applied: true });
  });

  it("returns success with syncedCount 0 when there's nothing pending", async () => {
    getPendingDeliveries.mockResolvedValue([]);
    const result = await runSync();
    expect(result).toMatchObject({ success: true, syncedCount: 0 });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("POSTs pending records to /sync with the access token as a Bearer header", async () => {
    const pending = [{ id: "1" }];
    getPendingDeliveries.mockResolvedValue(pending);
    getAccessToken.mockResolvedValue("tok-1");
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ resolved_records: [{ id: "1", status: "delivered" }], errors: [], conflicts: [] }),
    });

    await runSync();

    expect(global.fetch).toHaveBeenCalledWith(
      "http://test-server/sync",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer tok-1" },
      })
    );
    const sent = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(sent.records).toEqual([{ id: "1", local_revision: 0, base_version: 0 }]);
    // the device's clock at send time, so the server can correct event times for a wrong clock
    expect(Math.abs(new Date(sent.client_sent_at).getTime() - Date.now())).toBeLessThan(5000);
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1", status: "delivered" }, 0);
  });

  it("on a 401 refreshes the session once and retries with the new token", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    getAccessToken.mockResolvedValue("expired");
    refreshAccessToken.mockResolvedValue("fresh");
    global.fetch
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] }) });

    const result = await runSync();

    expect(result.success).toBe(true);
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(global.fetch.mock.calls[1][1].headers.Authorization).toBe("Bearer fresh");
  });

  it("keeps records queued and stops retrying when auth can't be restored", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    getAccessToken.mockResolvedValue("expired");
    refreshAccessToken.mockResolvedValue(null);
    global.fetch.mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });

    const result = await runSync();

    expect(result).toMatchObject({ success: false, authError: true });
    expect(global.fetch).toHaveBeenCalledTimes(1); // no 3x retry loop
    expect(markAsSyncedIfUnchanged).not.toHaveBeenCalled();
  });

  const okJson = (body) => ({ ok: true, status: 200, json: async () => body });

  it("does NOT report success when the server refused a record: it is blocked, with its reason", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", order_id: "ORD-1", local_revision: 2 }]);
    markBlockedIfUnchanged.mockResolvedValue({ applied: true, blocked: true });
    global.fetch.mockResolvedValue(okJson({
      resolved_records: [], conflicts: [],
      errors: [{ id: "1", error: "This delivery isn't assigned to you.", code: "not_assigned" }],
    }));

    const result = await runSync();

    expect(result).toMatchObject({ success: false, outcome: "failed", blockedCount: 1 });
    expect(markBlockedIfUnchanged).toHaveBeenCalledWith(
      "1",
      expect.objectContaining({ code: "not_assigned", kind: "permanent", actions: ["discard"] }),
      2
    );
  });

  it("reports partial_success when some records synced and one was blocked", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }, { id: "2", local_revision: 1 }]);
    markBlockedIfUnchanged.mockResolvedValue({ applied: true, blocked: true });
    global.fetch.mockResolvedValue(okJson({
      resolved_records: [{ id: "1" }], conflicts: [],
      errors: [{ id: "2", error: "gone", code: "not_found" }],
    }));
    expect(await runSync()).toMatchObject({ outcome: "partial_success", success: false, syncedCount: 1, blockedCount: 1 });
  });

  it("counts a retryable error and blocks the record once it has failed too many times", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", order_id: "ORD-1", local_revision: 1 }]);
    global.fetch.mockResolvedValue(okJson({ resolved_records: [], conflicts: [], errors: [{ id: "1", error: "busy", code: "concurrent_update" }] }));

    markRetryableFailureIfUnchanged.mockResolvedValueOnce({ applied: true, blocked: false, record: { sync_attempts: 2 } });
    expect(await runSync()).toMatchObject({ outcome: "failed", retryingCount: 1, blockedCount: 0 });

    markRetryableFailureIfUnchanged.mockResolvedValueOnce({ applied: true, blocked: true, record: { sync_attempts: 5, sync_error: { code: "retry_exhausted", message: "busy (failed 5 times)" } } });
    expect(await runSync()).toMatchObject({ blockedCount: 1, retryingCount: 0 });
  });

  it("stores a server-resolved conflict as conflicted and does not call it plain success", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }]);
    markConflictedIfUnchanged.mockResolvedValue({ applied: true });
    const conflict = { id: "1", order_id: "ORD-1", message: "Cancelled meanwhile.", your_status: "a", kept_status: "b" };
    global.fetch.mockResolvedValue(okJson({ resolved_records: [{ id: "1", status: "cancelled" }], errors: [], conflicts: [conflict] }));

    const result = await runSync();

    expect(markConflictedIfUnchanged).toHaveBeenCalledWith("1", { id: "1", status: "cancelled" }, conflict, 1);
    expect(markAsSyncedIfUnchanged).not.toHaveBeenCalled();
    expect(result).toMatchObject({ outcome: "partial_success", success: false, conflictCount: 1 });
  });

  it("a 422 naming one record blocks only that record; the valid ones still sync", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "a", local_revision: 1 }, { id: "b", local_revision: 4 }, { id: "c", local_revision: 1 }]);
    markBlockedIfUnchanged.mockResolvedValue({ applied: true, blocked: true });
    global.fetch
      .mockResolvedValueOnce({ ok: false, status: 422, json: async () => ({ detail: [{ loc: ["body", "records", 1, "status"], msg: "bad status" }] }) })
      .mockResolvedValueOnce(okJson({ resolved_records: [{ id: "a" }, { id: "c" }], errors: [], conflicts: [] }));

    const result = await runSync();

    expect(markBlockedIfUnchanged).toHaveBeenCalledTimes(1);
    expect(markBlockedIfUnchanged).toHaveBeenCalledWith("b", expect.objectContaining({ code: "invalid_record", message: "status: bad status" }), 4);
    expect(JSON.parse(global.fetch.mock.calls[1][1].body).records.map((r) => r.id)).toEqual(["a", "c"]);
    expect(result).toMatchObject({ outcome: "partial_success", syncedCount: 2, blockedCount: 1 });
  });

  it("finds the bad record by splitting the batch when the server doesn't say which one", async () => {
    getPendingDeliveries.mockResolvedValue(["a", "b", "c", "d"].map((id) => ({ id, local_revision: 1 })));
    markBlockedIfUnchanged.mockResolvedValue({ applied: true, blocked: true });
    global.fetch.mockImplementation(async (url, init) => {
      const sent = JSON.parse(init.body).records;
      if (sent.some((r) => r.id === "c")) return { ok: false, status: 400, json: async () => ({ detail: "bad request" }) };
      return okJson({ resolved_records: sent.map((r) => ({ id: r.id })), errors: [], conflicts: [] });
    });

    const result = await runSync();

    expect(markBlockedIfUnchanged.mock.calls.map((c) => c[0])).toEqual(["c"]);
    expect(result).toMatchObject({ outcome: "partial_success", syncedCount: 3, blockedCount: 1 });
  });

  it("does not blame records for a wrong URL or server outage (404/500): nothing is blocked", async () => {
    jest.useFakeTimers();
    getPendingDeliveries.mockResolvedValue([{ id: "a", local_revision: 1 }, { id: "b", local_revision: 1 }]);
    global.fetch.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });

    const promise = runSync();
    await jest.advanceTimersByTimeAsync(3000);
    await jest.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(markBlockedIfUnchanged).not.toHaveBeenCalled();
    expect(result).toMatchObject({ outcome: "failed", requestFailed: true });
    jest.useRealTimers();
  });

  it("uploads a large backlog in batches under the server's per-request limit", async () => {
    getPendingDeliveries.mockResolvedValue(Array.from({ length: 250 }, (_, i) => ({ id: `r${i}`, local_revision: 1 })));
    global.fetch.mockImplementation(async (url, init) => okJson({ resolved_records: JSON.parse(init.body).records.map((r) => ({ id: r.id })), errors: [], conflicts: [] }));

    const result = await runSync();

    expect(global.fetch.mock.calls.map((c) => JSON.parse(c[1].body).records.length)).toEqual([100, 100, 50]);
    expect(result).toMatchObject({ outcome: "success", syncedCount: 250 });
  });

  it("retries on failure and eventually succeeds", async () => {
    jest.useFakeTimers();
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    global.fetch
      .mockRejectedValueOnce(new TypeError("Network request failed"))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] }) });

    const resultPromise = runSync();
    await jest.advanceTimersByTimeAsync(3000);
    const result = await resultPromise;

    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(result.success).toBe(true);
    jest.useRealTimers();
  });

  it("gives up after 3 consecutive failures and reports the last error", async () => {
    jest.useFakeTimers();
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    global.fetch.mockRejectedValue(new TypeError("Network request failed"));

    const resultPromise = runSync();
    await jest.advanceTimersByTimeAsync(3000);
    await jest.advanceTimersByTimeAsync(3000);
    const result = await resultPromise;

    expect(global.fetch).toHaveBeenCalledTimes(3);
    expect(result).toMatchObject({ outcome: "failed", success: false, syncedCount: 0, error: "Network request failed", requestFailed: true });
    jest.useRealTimers();
  });

  it("throws a plain error (not silently swallowed) when the server responds but not ok", async () => {
    jest.useFakeTimers();
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    global.fetch.mockResolvedValue({ ok: false, json: async () => ({}) });

    const resultPromise = runSync();
    await jest.advanceTimersByTimeAsync(3000);
    await jest.advanceTimersByTimeAsync(3000);
    const result = await resultPromise;

    expect(result.success).toBe(false);
    expect(result.error).toBe("Sync request failed");
    jest.useRealTimers();
  });
});

describe("runSync — lost-update protection", () => {
  const okResponse = (body) => ({ ok: true, status: 200, json: async () => body });

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
    getAccessToken.mockResolvedValue("tok");
    markAsSyncedIfUnchanged.mockResolvedValue({ applied: true });
  });

  it("only one sync runs at a time: a call made mid-flight is skipped, not duplicated", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }]);
    let finish;
    global.fetch.mockImplementation(() => new Promise((resolve) => { finish = () => resolve(okResponse({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] })); }));

    const first = runSync();
    await new Promise((r) => setImmediate(r));
    const second = await runSync();

    expect(second).toEqual({ outcome: "skipped", success: true, syncedCount: 0, skipped: true });
    expect(global.fetch).toHaveBeenCalledTimes(1);
    finish();
    expect((await first).success).toBe(true);
    // lock released afterwards
    getPendingDeliveries.mockResolvedValue([]);
    expect((await runSync()).skipped).toBeUndefined();
  });

  it("sends each record's local_revision and writes back against the revision that was sent", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 4 }]);
    global.fetch.mockResolvedValue(okResponse({ resolved_records: [{ id: "1", status: "delivered" }], errors: [], conflicts: [] }));

    await runSync();

    expect(JSON.parse(global.fetch.mock.calls[0][1].body).records[0].local_revision).toBe(4);
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1", status: "delivered" }, 4);
  });

  it("prefers the revision the server acknowledged", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 4 }]);
    global.fetch.mockResolvedValue(okResponse({ resolved_records: [{ id: "1" }], errors: [], conflicts: [], acknowledged: [{ id: "1", local_revision: 3 }] }));
    await runSync();
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1" }, 3);
  });

  it("re-reads the pending queue before every retry instead of re-sending a stale snapshot", async () => {
    jest.useFakeTimers();
    getPendingDeliveries
      .mockResolvedValueOnce([{ id: "1", status: "picked_up", local_revision: 1 }])
      .mockResolvedValueOnce([{ id: "1", status: "delivered", local_revision: 2 }]);
    global.fetch
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(okResponse({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] }));

    const promise = runSync();
    await jest.advanceTimersByTimeAsync(3000);
    await promise;
    jest.useRealTimers();

    expect(JSON.parse(global.fetch.mock.calls[1][1].body).records[0]).toMatchObject({ status: "delivered", local_revision: 2 });
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1" }, 2);
  });

  it("a record edited mid-flight stays pending and is uploaded by a follow-up pass", async () => {
    getPendingDeliveries
      .mockResolvedValueOnce([{ id: "1", status: "picked_up", local_revision: 1 }])
      .mockResolvedValueOnce([{ id: "1", status: "delivered", local_revision: 2 }]);
    global.fetch.mockResolvedValue(okResponse({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] }));
    markAsSyncedIfUnchanged
      .mockResolvedValueOnce({ applied: false, reason: "changed_during_sync" })
      .mockResolvedValueOnce({ applied: true });

    const result = await runSync();

    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(global.fetch.mock.calls[1][1].body).records[0]).toMatchObject({ status: "delivered", local_revision: 2 });
    // Only writes that were actually applied count: pass 1 was deferred (a newer edit existed), pass 2 settled it.
    expect(result).toMatchObject({ success: true, syncedCount: 1 });
  });

  it("bounds follow-up passes so a constantly edited record can't loop forever", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }]);
    global.fetch.mockResolvedValue(okResponse({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] }));
    markAsSyncedIfUnchanged.mockResolvedValue({ applied: false, reason: "changed_during_sync" });

    await runSync();

    expect(global.fetch).toHaveBeenCalledTimes(4); // 1 pass + 3 follow-ups
  });
});

describe("sync state for the UI", () => {
  beforeEach(() => jest.clearAllMocks());

  it("getSyncSummary counts pending, syncing, synced, conflicted and blocked", async () => {
    getCachedDeliveries.mockResolvedValue([
      { id: "1", sync_status: "pending" }, { id: "2", sync_status: "synced" },
      { id: "3", sync_status: "blocked" }, { id: "4", sync_status: "conflicted" },
    ]);
    expect(await getSyncSummary()).toMatchObject({ pending: 1, syncing: 0, synced: 1, blocked: 1, conflicted: 1, needsAttention: 2 });
  });

  it("retryBlocked re-queues the record; discardBlocked removes it from this device only", async () => {
    retryBlockedRecord.mockResolvedValue({ applied: true });
    await retryBlocked("x");
    expect(retryBlockedRecord).toHaveBeenCalledWith("x");
    await discardBlocked("y");
    expect(discardLocalRecord).toHaveBeenCalledWith("y");
  });
});

describe("startAutoSync", () => {
  let appStateListeners;

  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
    global.fetch = jest.fn();
    appStateListeners = [];
    jest.spyOn(AppState, "addEventListener").mockImplementation((event, handler) => {
      appStateListeners.push(handler);
      return { remove: jest.fn() };
    });
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("triggers a sync immediately on startup when connected", async () => {
    Network.getNetworkStateAsync.mockResolvedValue({ isConnected: true, isInternetReachable: true });
    getPendingDeliveries.mockResolvedValue([]);

    const stop = startAutoSync();
    await jest.runOnlyPendingTimersAsync();

    expect(getPendingDeliveries).toHaveBeenCalled();
    stop();
  });

  it("does not sync on startup when not connected", async () => {
    Network.getNetworkStateAsync.mockResolvedValue({ isConnected: false, isInternetReachable: false });

    const stop = startAutoSync();
    await jest.runOnlyPendingTimersAsync();

    expect(getPendingDeliveries).not.toHaveBeenCalled();
    stop();
  });

  it("triggers a sync when the app becomes active again", async () => {
    Network.getNetworkStateAsync.mockResolvedValue({ isConnected: true, isInternetReachable: true });
    getPendingDeliveries.mockResolvedValue([]);

    const stop = startAutoSync();
    await jest.runOnlyPendingTimersAsync();
    getPendingDeliveries.mockClear();

    // Simulate the registered AppState listener firing with "active".
    appStateListeners.forEach((handler) => handler("active"));
    await jest.runOnlyPendingTimersAsync();

    expect(getPendingDeliveries).toHaveBeenCalled();
    stop();
  });

  it("does not trigger a sync when the app goes to background", async () => {
    Network.getNetworkStateAsync.mockResolvedValue({ isConnected: true, isInternetReachable: true });
    getPendingDeliveries.mockResolvedValue([]);

    const stop = startAutoSync();
    await jest.runOnlyPendingTimersAsync();
    getPendingDeliveries.mockClear();

    // Flush microtasks only — deliberately NOT advancing fake timers
    // here, since doing so would also let the independent periodic
    // sync interval fire and contaminate this assertion with an
    // unrelated trigger path.
    appStateListeners.forEach((handler) => handler("background"));
    await Promise.resolve();
    await Promise.resolve();

    expect(getPendingDeliveries).not.toHaveBeenCalled();
    stop();
  });

  it("returns a cleanup function that removes the AppState listener and stops the interval", async () => {
    Network.getNetworkStateAsync.mockResolvedValue({ isConnected: true, isInternetReachable: true });
    getPendingDeliveries.mockResolvedValue([]);
    const removeSpy = jest.fn();
    AppState.addEventListener.mockReturnValue({ remove: removeSpy });

    const stop = startAutoSync();
    await jest.runOnlyPendingTimersAsync();
    stop();

    expect(removeSpy).toHaveBeenCalled();
  });
});
