import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mocked BEFORE importing syncEngine so its own top-level `import`s of
// these modules resolve to the mocks below — vi.mock is hoisted above
// imports by Vitest specifically to make this ordering work.
vi.mock("../indexedDb", () => ({
  getPendingDeliveries: vi.fn(),
  getAllLocalDeliveries: vi.fn(),
  markAsSyncedIfUnchanged: vi.fn(),
  markBlockedIfUnchanged: vi.fn(),
  markRetryableFailureIfUnchanged: vi.fn(),
  markConflictedIfUnchanged: vi.fn(),
  retryBlockedRecord: vi.fn(),
  acknowledgeConflict: vi.fn(),
  deleteDeliveryLocally: vi.fn(),
  getSyncLockName: () => "delivery-sync:test",
}));
vi.mock("../api", () => ({
  syncPendingDeliveries: vi.fn(),
}));
vi.mock("../backgroundSync", () => ({
  requestBackgroundSync: vi.fn(),
}));

import { describeConflict, runSync, startAutoSync, getSyncSummary, retryBlocked, discardBlocked } from "../syncEngine";
import {
  getPendingDeliveries, getAllLocalDeliveries, markAsSyncedIfUnchanged, markBlockedIfUnchanged,
  markRetryableFailureIfUnchanged, markConflictedIfUnchanged, retryBlockedRecord, deleteDeliveryLocally,
} from "../indexedDb";
import { syncPendingDeliveries } from "../api";
import { requestBackgroundSync } from "../backgroundSync";

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

  it("uses the server's own explanation when it gives one (version checks, not 'who is newer')", () => {
    const message = describeConflict({
      order_id: "ORD-3",
      code: "stale_version",
      message: "This delivery was cancelled after you last synced, so your change to 'delivered' was not applied.",
      your_status: "delivered",
      kept_status: "cancelled",
    });
    expect(message).toBe("Order ORD-3: This delivery was cancelled after you last synced, so your change to 'delivered' was not applied.");
  });

  it("falls back to a generic phrase when kept_by is missing", () => {
    const message = describeConflict({
      order_id: "ORD-2",
      your_status: "out_for_delivery",
      kept_status: "delivered",
      kept_by: null,
    });
    expect(message).toContain("it was already updated");
    expect(message).toContain("ORD-2");
  });
});

describe("runSync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    markAsSyncedIfUnchanged.mockResolvedValue({ applied: true });
  });

  it("stops immediately on a 401 (no retry loop) and leaves records pending", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    const authError = new Error("Session expired — sign in again to sync.");
    authError.status = 401;
    syncPendingDeliveries.mockRejectedValue(authError);

    const result = await runSync();

    expect(result).toMatchObject({ success: false, authError: true });
    expect(syncPendingDeliveries).toHaveBeenCalledTimes(1);
    expect(markAsSyncedIfUnchanged).not.toHaveBeenCalled();
  });

  it("returns success with syncedCount 0 when there's nothing pending", async () => {
    getPendingDeliveries.mockResolvedValue([]);
    const result = await runSync();
    expect(result).toMatchObject({ success: true, syncedCount: 0 });
    expect(syncPendingDeliveries).not.toHaveBeenCalled();
  });

  it("syncs pending records and reconciles each resolved record into IndexedDB", async () => {
    const pending = [{ id: "1" }, { id: "2" }];
    getPendingDeliveries.mockResolvedValue(pending);
    syncPendingDeliveries.mockResolvedValue({
      resolved_records: [{ id: "1", status: "delivered" }, { id: "2", status: "picked_up" }],
      errors: [],
      conflicts: [],
    });

    const result = await runSync();

    // Each record is sent WITH its local revision (0 = a record that predates revisions).
    expect(syncPendingDeliveries).toHaveBeenCalledWith([
      { id: "1", local_revision: 0, base_version: 0 },
      { id: "2", local_revision: 0, base_version: 0 },
    ]);
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledTimes(2);
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1", status: "delivered" }, 0);
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("2", { id: "2", status: "picked_up" }, 0);
    expect(result).toMatchObject({ success: true, syncedCount: 2, errorCount: 0 });
  });

  it("does NOT report success when the server refused a record: it is blocked and the run is not a success", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", order_id: "ORD-1", local_revision: 2 }]);
    markBlockedIfUnchanged.mockResolvedValue({ applied: true, blocked: true });
    syncPendingDeliveries.mockResolvedValue({
      resolved_records: [],
      errors: [{ id: "1", error: "This delivery isn't assigned to you.", code: "not_assigned" }],
      conflicts: [],
    });

    const result = await runSync();

    expect(result.success).toBe(false);
    expect(result.outcome).toBe("failed"); // nothing could be settled
    expect(result.blockedCount).toBe(1);
    expect(result.blocked[0]).toMatchObject({ id: "1", order_id: "ORD-1", code: "not_assigned" });
    // Quarantined with its reason, at the revision that was sent — so it can never be re-sent in a loop.
    expect(markBlockedIfUnchanged).toHaveBeenCalledWith(
      "1",
      expect.objectContaining({ code: "not_assigned", kind: "permanent", message: "This delivery isn't assigned to you.", actions: ["discard"] }),
      2
    );
  });

  it("reports partial_success when some records synced and one was blocked", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }, { id: "2", local_revision: 1 }]);
    markBlockedIfUnchanged.mockResolvedValue({ applied: true, blocked: true });
    syncPendingDeliveries.mockResolvedValue({
      resolved_records: [{ id: "1", status: "delivered" }],
      errors: [{ id: "2", error: "Delivery not found.", code: "not_found" }],
      conflicts: [],
    });

    const result = await runSync();

    expect(result).toMatchObject({ outcome: "partial_success", success: false, syncedCount: 1, blockedCount: 1 });
  });

  it("a retryable record error is counted (not blocked) while attempts remain", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", order_id: "ORD-1", local_revision: 1 }]);
    markRetryableFailureIfUnchanged.mockResolvedValue({ applied: true, blocked: false, record: { sync_attempts: 2 } });
    syncPendingDeliveries.mockResolvedValue({
      resolved_records: [],
      errors: [{ id: "1", error: "changed at the same moment", code: "concurrent_update" }],
      conflicts: [],
    });

    const result = await runSync();

    expect(markBlockedIfUnchanged).not.toHaveBeenCalled();
    expect(result).toMatchObject({ outcome: "failed", success: false, retryingCount: 1, blockedCount: 0 });
    expect(result.retrying[0]).toMatchObject({ id: "1", attempts: 2 });
    expect(requestBackgroundSync).toHaveBeenCalled(); // retryable work remains: ask the browser to wake the service worker later
  });

  it("a retryable error that has failed too many times is reported as blocked", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", order_id: "ORD-1", local_revision: 1 }]);
    markRetryableFailureIfUnchanged.mockResolvedValue({
      applied: true, blocked: true,
      record: { sync_attempts: 5, sync_error: { code: "retry_exhausted", message: "boom (failed 5 times)" } },
    });
    syncPendingDeliveries.mockResolvedValue({ resolved_records: [], errors: [{ id: "1", error: "boom", code: "concurrent_update" }], conflicts: [] });

    const result = await runSync();
    expect(result.blocked[0]).toMatchObject({ id: "1", code: "retry_exhausted" });
    expect(result.retryingCount).toBe(0);
  });

  it("an error code nobody anticipated is treated as retryable, so it is bounded by attempts instead of looping silently", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }]);
    markRetryableFailureIfUnchanged.mockResolvedValue({ applied: true, blocked: false, record: { sync_attempts: 1 } });
    syncPendingDeliveries.mockResolvedValue({ resolved_records: [], errors: [{ id: "1", message: "weird" }], conflicts: [] });

    await runSync();
    expect(markRetryableFailureIfUnchanged).toHaveBeenCalledWith("1", expect.objectContaining({ code: "unknown_error", kind: "retryable" }), 1);
  });

  it("a server-resolved conflict is stored as conflicted, surfaced, and is not plain success", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }]);
    markConflictedIfUnchanged.mockResolvedValue({ applied: true });
    const conflict = { id: "1", order_id: "ORD-1", code: "stale_version", message: "Cancelled after you last synced.", your_status: "delivered", kept_status: "cancelled" };
    syncPendingDeliveries.mockResolvedValue({ resolved_records: [{ id: "1", status: "cancelled" }], errors: [], conflicts: [conflict] });

    const result = await runSync();

    expect(markConflictedIfUnchanged).toHaveBeenCalledWith("1", { id: "1", status: "cancelled" }, conflict, 1);
    expect(markAsSyncedIfUnchanged).not.toHaveBeenCalled();
    expect(result).toMatchObject({ outcome: "partial_success", success: false, conflictCount: 1, syncedCount: 0 });
    expect(result.conflicts).toHaveLength(1);
  });

  it("conflicts that can't be tied to a stored record are still reported", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    syncPendingDeliveries.mockResolvedValue({
      resolved_records: [{ id: "1" }], errors: [],
      conflicts: [{ order_id: "ORD-1", your_status: "a", kept_status: "b", kept_by: "X" }],
    });
    const result = await runSync();
    expect(result.conflicts).toHaveLength(1);
    expect(result.syncedCount).toBe(1);
  });

  it("only sends PENDING records: blocked and conflicted ones are never re-sent (the queue read is pending-only)", async () => {
    getPendingDeliveries.mockResolvedValue([]);
    const result = await runSync();
    expect(result).toMatchObject({ outcome: "success", success: true, syncedCount: 0 });
    expect(syncPendingDeliveries).not.toHaveBeenCalled();
  });

  it("retries on failure and eventually succeeds without exhausting retries", async () => {
    vi.useFakeTimers();
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    syncPendingDeliveries
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] });

    const resultPromise = runSync();
    // Let the first attempt's rejection be handled, then fast-forward
    // past the fixed retry delay so the second attempt actually runs.
    await vi.advanceTimersByTimeAsync(3000);
    const result = await resultPromise;

    expect(syncPendingDeliveries).toHaveBeenCalledTimes(2);
    expect(result.success).toBe(true);
    vi.useRealTimers();
  });

  it("gives up after MAX_RETRIES consecutive failures and reports the last error", async () => {
    vi.useFakeTimers();
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    syncPendingDeliveries.mockRejectedValue(new Error("still down"));

    const resultPromise = runSync();
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    const result = await resultPromise;

    expect(syncPendingDeliveries).toHaveBeenCalledTimes(3);
    expect(result).toMatchObject({ outcome: "failed", success: false, syncedCount: 0, error: "still down", requestFailed: true });
    expect(requestBackgroundSync).toHaveBeenCalled(); // a network failure is retryable: re-register background work
    vi.useRealTimers();
  });
});

describe("runSync — lost-update protection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    markAsSyncedIfUnchanged.mockResolvedValue({ applied: true });
  });

  it("only one sync runs at a time: a call made mid-flight is skipped, not duplicated", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }]);
    let finishRequest;
    syncPendingDeliveries.mockImplementation(
      () => new Promise((resolve) => { finishRequest = () => resolve({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] }); })
    );

    const first = runSync();
    await vi.waitFor(() => expect(syncPendingDeliveries).toHaveBeenCalledTimes(1));

    const second = await runSync(); // fires while the first request is still in the air
    expect(second).toEqual({ outcome: "skipped", success: true, syncedCount: 0, skipped: true });
    expect(syncPendingDeliveries).toHaveBeenCalledTimes(1);

    finishRequest();
    expect((await first).success).toBe(true);
  });

  it("releases the lock after a failure so the next sync can run", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1" }]);
    const authError = new Error("expired");
    authError.status = 401;
    syncPendingDeliveries.mockRejectedValueOnce(authError);
    expect((await runSync()).authError).toBe(true);

    syncPendingDeliveries.mockResolvedValueOnce({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] });
    const again = await runSync();
    expect(again.skipped).toBeUndefined();
    expect(again.success).toBe(true);
  });

  it("re-reads the pending queue before EVERY retry instead of re-sending a stale snapshot", async () => {
    vi.useFakeTimers();
    getPendingDeliveries
      .mockResolvedValueOnce([{ id: "1", status: "picked_up", local_revision: 1 }])
      .mockResolvedValueOnce([{ id: "1", status: "delivered", local_revision: 2 }]); // edited while we were retrying
    syncPendingDeliveries
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] });

    const resultPromise = runSync();
    await vi.advanceTimersByTimeAsync(3000);
    await resultPromise;

    expect(syncPendingDeliveries.mock.calls[0][0][0]).toMatchObject({ status: "picked_up", local_revision: 1 });
    expect(syncPendingDeliveries.mock.calls[1][0][0]).toMatchObject({ status: "delivered", local_revision: 2 });
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1" }, 2); // acknowledged against the revision actually sent
    vi.useRealTimers();
  });

  it("sends the SERVER version each record was read at as base_version", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", version: 7, local_revision: 2 }, { id: "2", version: 1, local_revision: 1 }]);
    syncPendingDeliveries.mockResolvedValue({ resolved_records: [], errors: [], conflicts: [] });
    await runSync();
    expect(syncPendingDeliveries).toHaveBeenCalledWith([
      { id: "1", version: 7, local_revision: 2, base_version: 7 },
      { id: "2", version: 1, local_revision: 1, base_version: 1 },
    ]);
  });

  it("passes the SUBMITTED revision when writing back, and never forces a write over a newer edit", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 4 }]);
    syncPendingDeliveries.mockResolvedValue({ resolved_records: [{ id: "1", status: "delivered" }], errors: [], conflicts: [] });
    await runSync();
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1", status: "delivered" }, 4);
  });

  it("prefers the revision the server acknowledged over the locally remembered one", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 4 }]);
    syncPendingDeliveries.mockResolvedValue({
      resolved_records: [{ id: "1" }], errors: [], conflicts: [], acknowledged: [{ id: "1", local_revision: 3 }],
    });
    await runSync();
    expect(markAsSyncedIfUnchanged).toHaveBeenCalledWith("1", { id: "1" }, 3);
  });

  it("when the record changed mid-flight it stays pending and a follow-up pass uploads the newer edit", async () => {
    getPendingDeliveries
      .mockResolvedValueOnce([{ id: "1", status: "picked_up", local_revision: 1 }])
      .mockResolvedValueOnce([{ id: "1", status: "delivered", local_revision: 2 }]);
    syncPendingDeliveries.mockResolvedValue({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] });
    markAsSyncedIfUnchanged
      .mockResolvedValueOnce({ applied: false, reason: "changed_during_sync" }) // pass 1: newer edit exists
      .mockResolvedValueOnce({ applied: true });                               // pass 2: now settled

    const result = await runSync();

    expect(syncPendingDeliveries).toHaveBeenCalledTimes(2);
    expect(syncPendingDeliveries.mock.calls[1][0][0]).toMatchObject({ status: "delivered", local_revision: 2 });
    // Only writes that were actually applied count: pass 1 was deferred (a newer edit existed), pass 2 settled it.
    expect(result).toMatchObject({ success: true, syncedCount: 1 });
  });

  it("bounds the follow-up passes so a constantly-edited record can't loop forever", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "1", local_revision: 1 }]);
    syncPendingDeliveries.mockResolvedValue({ resolved_records: [{ id: "1" }], errors: [], conflicts: [] });
    markAsSyncedIfUnchanged.mockResolvedValue({ applied: false, reason: "changed_during_sync" });

    const result = await runSync();

    expect(syncPendingDeliveries).toHaveBeenCalledTimes(4); // 1 pass + 3 follow-ups
    expect(result.success).toBe(true);
  });
});

describe("runSync — a poisoned batch must not block the valid records", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    markAsSyncedIfUnchanged.mockResolvedValue({ applied: true });
    markBlockedIfUnchanged.mockResolvedValue({ applied: true, blocked: true });
  });

  function rejection(status, detail) {
    const error = new Error("Sync request failed");
    error.status = status;
    error.detail = detail;
    return error;
  }

  it("blocks exactly the record the server names in a 422 and still syncs the rest", async () => {
    const records = [{ id: "a", local_revision: 1 }, { id: "b", local_revision: 3 }, { id: "c", local_revision: 1 }];
    getPendingDeliveries.mockResolvedValue(records);
    syncPendingDeliveries
      .mockRejectedValueOnce(rejection(422, [{ loc: ["body", "records", 1, "status"], msg: "Input should be a valid status", type: "enum" }]))
      .mockResolvedValueOnce({ resolved_records: [{ id: "a" }, { id: "c" }], errors: [], conflicts: [] });

    const result = await runSync();

    expect(markBlockedIfUnchanged).toHaveBeenCalledTimes(1);
    expect(markBlockedIfUnchanged).toHaveBeenCalledWith(
      "b", expect.objectContaining({ code: "invalid_record", message: "status: Input should be a valid status" }), 3
    );
    expect(syncPendingDeliveries).toHaveBeenCalledTimes(2);
    expect(syncPendingDeliveries.mock.calls[1][0].map((r) => r.id)).toEqual(["a", "c"]); // the poisoned record is NOT resent
    expect(result).toMatchObject({ outcome: "partial_success", syncedCount: 2, blockedCount: 1 });
  });

  it("finds the bad record by splitting the batch when the server doesn't say which one", async () => {
    const records = ["a", "b", "c", "d"].map((id) => ({ id, local_revision: 1 }));
    getPendingDeliveries.mockResolvedValue(records);
    syncPendingDeliveries.mockImplementation(async (sent) => {
      if (sent.some((r) => r.id === "c")) throw rejection(400, "bad request");
      return { resolved_records: sent.map((r) => ({ id: r.id })), errors: [], conflicts: [] };
    });

    const result = await runSync();

    expect(markBlockedIfUnchanged).toHaveBeenCalledTimes(1);
    expect(markBlockedIfUnchanged.mock.calls[0][0]).toBe("c");
    expect(markBlockedIfUnchanged.mock.calls[0][1]).toMatchObject({ code: "rejected_by_server", message: "bad request" });
    expect(result).toMatchObject({ outcome: "partial_success", syncedCount: 3, blockedCount: 1 });
  });

  it("a single record the server refuses outright is blocked, not retried", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "a", local_revision: 1 }]);
    syncPendingDeliveries.mockRejectedValue(rejection(422, "nope"));

    const result = await runSync();

    expect(syncPendingDeliveries).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({ outcome: "failed", blockedCount: 1 });
  });

  it("does NOT blame records for a server outage or a wrong URL (404/500): nothing is blocked, work is retried later", async () => {
    vi.useFakeTimers();
    getPendingDeliveries.mockResolvedValue([{ id: "a", local_revision: 1 }, { id: "b", local_revision: 1 }]);
    syncPendingDeliveries.mockRejectedValue(rejection(404, undefined));

    const promise = runSync();
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(markBlockedIfUnchanged).not.toHaveBeenCalled();
    expect(result).toMatchObject({ outcome: "failed", requestFailed: true });
    expect(requestBackgroundSync).toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("an expired session is reported as an auth failure and does not ask for background retries", async () => {
    getPendingDeliveries.mockResolvedValue([{ id: "a" }]);
    syncPendingDeliveries.mockRejectedValue(rejection(401, undefined));
    const result = await runSync();
    expect(result).toMatchObject({ outcome: "failed", authError: true });
    expect(requestBackgroundSync).not.toHaveBeenCalled();
  });

  it("uploads a large backlog in batches under the server's per-request limit", async () => {
    const records = Array.from({ length: 250 }, (_, i) => ({ id: `r${i}`, local_revision: 1 }));
    getPendingDeliveries.mockResolvedValue(records);
    syncPendingDeliveries.mockImplementation(async (sent) => ({ resolved_records: sent.map((r) => ({ id: r.id })), errors: [], conflicts: [] }));

    const result = await runSync();

    expect(syncPendingDeliveries.mock.calls.map((c) => c[0].length)).toEqual([100, 100, 50]);
    expect(result).toMatchObject({ outcome: "success", syncedCount: 250 });
  });

  it("keeps the progress of earlier batches when a later one fails at the request level", async () => {
    vi.useFakeTimers();
    const records = Array.from({ length: 150 }, (_, i) => ({ id: `r${i}`, local_revision: 1 }));
    getPendingDeliveries.mockResolvedValue(records);
    syncPendingDeliveries
      .mockResolvedValueOnce({ resolved_records: records.slice(0, 100).map((r) => ({ id: r.id })), errors: [], conflicts: [] })
      .mockRejectedValue(new Error("network down"));

    const promise = runSync();
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    const result = await promise;

    expect(result).toMatchObject({ outcome: "partial_success", requestFailed: true });
    expect(result.syncedCount).toBeGreaterThan(0);
    vi.useRealTimers();
  });
});

describe("sync state for the UI", () => {
  beforeEach(() => vi.clearAllMocks());

  it("getSyncSummary counts pending, syncing, synced, conflicted and blocked", async () => {
    getAllLocalDeliveries.mockResolvedValue([
      { id: "1", sync_status: "pending" },
      { id: "2", sync_status: "synced" },
      { id: "3", sync_status: "blocked" },
      { id: "4", sync_status: "conflicted" },
    ]);
    expect(await getSyncSummary()).toMatchObject({ pending: 1, syncing: 0, synced: 1, blocked: 1, conflicted: 1, needsAttention: 2 });
  });

  it("retryBlocked re-queues the record; discardBlocked removes it from this device only", async () => {
    retryBlockedRecord.mockResolvedValue({ applied: true });
    await retryBlocked("x");
    expect(retryBlockedRecord).toHaveBeenCalledWith("x");

    await discardBlocked("y");
    expect(deleteDeliveryLocally).toHaveBeenCalledWith("y");
  });
});

describe("startAutoSync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("triggers a sync immediately on startup while online", async () => {
    Object.defineProperty(navigator, "onLine", { value: true, configurable: true });
    getPendingDeliveries.mockResolvedValue([]);

    const stop = startAutoSync();
    await vi.runOnlyPendingTimersAsync();

    expect(getPendingDeliveries).toHaveBeenCalled();
    stop();
  });

  it("does not sync on startup while offline", async () => {
    Object.defineProperty(navigator, "onLine", { value: false, configurable: true });

    const stop = startAutoSync();
    await vi.runOnlyPendingTimersAsync();

    expect(getPendingDeliveries).not.toHaveBeenCalled();
    stop();
  });

  it("calls onSyncComplete with the sync result", async () => {
    Object.defineProperty(navigator, "onLine", { value: true, configurable: true });
    getPendingDeliveries.mockResolvedValue([]);
    const onSyncComplete = vi.fn();

    const stop = startAutoSync(onSyncComplete);
    await vi.runOnlyPendingTimersAsync();

    expect(onSyncComplete).toHaveBeenCalledWith(expect.objectContaining({ success: true, syncedCount: 0 }));
    stop();
  });

  it("returns a cleanup function that removes the online listener and stops the interval", () => {
    Object.defineProperty(navigator, "onLine", { value: true, configurable: true });
    getPendingDeliveries.mockResolvedValue([]);
    const removeSpy = vi.spyOn(window, "removeEventListener");

    const stop = startAutoSync();
    stop();

    expect(removeSpy).toHaveBeenCalledWith("online", expect.any(Function));
  });
});
