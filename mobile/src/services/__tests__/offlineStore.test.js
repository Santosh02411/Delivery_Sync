/**
 * Tests offlineStore.js against a real (in-memory, jest-expo-provided)
 * AsyncStorage mock — not a hand-rolled stub — so these tests exercise
 * the actual read/write/JSON-serialize round trip, not just whether
 * the right mock function got called.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  setActiveUser,
  cacheDeliveries,
  getCachedDeliveries,
  getCachedDelivery,
  queueStatusUpdate,
  getPendingDeliveries,
  getPendingCount,
  markAsSyncedIfUnchanged,
  markBlockedIfUnchanged,
  markRetryableFailureIfUnchanged,
  markConflictedIfUnchanged,
  retryBlockedRecord,
  acknowledgeConflict,
  discardLocalRecord,
  clearAll,
} from "../offlineStore";
import { classifyRecordError, MAX_RECORD_ATTEMPTS } from "../syncOutcome";

beforeEach(async () => {
  await AsyncStorage.clear();
  setActiveUser("agent-1");
});

describe("cacheDeliveries", () => {
  it("stores deliveries retrievable via getCachedDeliveries", async () => {
    await cacheDeliveries([
      { id: "d1", order_id: "ORD-1", status: "picked_up" },
      { id: "d2", order_id: "ORD-2", status: "pending" },
    ]);
    const cached = await getCachedDeliveries();
    expect(cached).toHaveLength(2);
    expect(cached.find((d) => d.id === "d1").sync_status).toBe("synced");
  });

  it("does not overwrite a locally-pending record with server data for the same id", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up" }]);
    await queueStatusUpdate("d1", { status: "out_for_delivery" });

    // A background refetch of the OLD server state must not clobber
    // the not-yet-synced local edit.
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up" }]);

    const record = await getCachedDelivery("d1");
    expect(record.status).toBe("out_for_delivery");
    expect(record.sync_status).toBe("pending");
  });

  it("scopes storage per user — a different user sees no data", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1" }]);
    setActiveUser("agent-2");
    expect(await getCachedDeliveries()).toEqual([]);
  });
});

describe("queueStatusUpdate", () => {
  it("applies the patch and marks the record pending", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up", agent_id: "agent-1" }]);
    const updated = await queueStatusUpdate("d1", { status: "delivered", updated_at: "2026-01-01T00:00:00Z" });

    expect(updated.status).toBe("delivered");
    expect(updated.sync_status).toBe("pending");
    expect(updated.agent_id).toBe("agent-1"); // untouched fields survive the patch
  });

  it("throws when queuing an update for a delivery that was never cached", async () => {
    await expect(queueStatusUpdate("unknown-id", { status: "delivered" })).rejects.toThrow(
      "never cached"
    );
  });
});

describe("getPendingDeliveries / getPendingCount", () => {
  it("returns only pending records, not synced ones", async () => {
    await cacheDeliveries([
      { id: "d1", order_id: "ORD-1" },
      { id: "d2", order_id: "ORD-2" },
    ]);
    await queueStatusUpdate("d1", { status: "delivered" });

    const pending = await getPendingDeliveries();
    expect(pending).toHaveLength(1);
    expect(pending[0].id).toBe("d1");
    expect(await getPendingCount()).toBe(1);
  });

  it("returns 0/empty when nothing is queued", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1" }]);
    expect(await getPendingCount()).toBe(0);
  });
});

describe("markAsSyncedIfUnchanged", () => {
  it("replaces the local record with the server's resolved version when it is unchanged since it was sent", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up" }]);
    const sent = await queueStatusUpdate("d1", { status: "out_for_delivery" });

    // Server resolved a conflict in favor of someone else's newer change.
    const outcome = await markAsSyncedIfUnchanged("d1", { id: "d1", order_id: "ORD-1", status: "delivered" }, sent.local_revision);

    expect(outcome).toEqual({ applied: true });
    const record = await getCachedDelivery("d1");
    expect(record.status).toBe("delivered");
    expect(record.sync_status).toBe("synced");
    expect(await getPendingCount()).toBe(0);
  });

  it("does NOT overwrite an edit made while the earlier sync request was in flight", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up" }]);
    const sent = await queueStatusUpdate("d1", { status: "out_for_delivery" }); // revision 1 is uploaded...
    await queueStatusUpdate("d1", { status: "delivered" });                      // ...then the agent edits again (revision 2)

    const outcome = await markAsSyncedIfUnchanged("d1", { id: "d1", order_id: "ORD-1", status: "out_for_delivery" }, sent.local_revision);

    expect(outcome).toEqual({ applied: false, reason: "changed_during_sync" });
    const record = await getCachedDelivery("d1");
    expect(record).toMatchObject({ status: "delivered", sync_status: "pending", local_revision: 2 });
    expect(await getPendingCount()).toBe(1); // still queued for the next pass
  });

  it("does not bring back a record that no longer exists locally", async () => {
    const outcome = await markAsSyncedIfUnchanged("gone", { id: "gone", status: "delivered" }, 1);
    expect(outcome).toEqual({ applied: false, reason: "deleted_locally" });
    expect(await getCachedDelivery("gone")).toBeNull();
  });
});

describe("local revisions and the write queue", () => {
  it("bumps local_revision on every queued edit", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up" }]);
    expect((await queueStatusUpdate("d1", { status: "out_for_delivery" })).local_revision).toBe(1);
    expect((await queueStatusUpdate("d1", { status: "delivered" })).local_revision).toBe(2);
  });

  it("keeps every change when many writes overlap (the whole cache is one AsyncStorage key)", async () => {
    await cacheDeliveries([
      { id: "d1", order_id: "ORD-1", status: "picked_up" },
      { id: "d2", order_id: "ORD-2", status: "picked_up" },
      { id: "d3", order_id: "ORD-3", status: "picked_up" },
    ]);

    // Fired at the same moment: two edits, a server refresh that adds a record, and a sync write-back.
    await Promise.all([
      queueStatusUpdate("d1", { status: "delivered" }),
      queueStatusUpdate("d2", { status: "delivered" }),
      cacheDeliveries([{ id: "d4", order_id: "ORD-4", status: "pending" }]),
      markAsSyncedIfUnchanged("d3", { id: "d3", order_id: "ORD-3", status: "delivered" }, 0),
    ]);

    const all = Object.fromEntries((await getCachedDeliveries()).map((d) => [d.id, d]));
    expect(all.d1).toMatchObject({ status: "delivered", sync_status: "pending" });
    expect(all.d2).toMatchObject({ status: "delivered", sync_status: "pending" });
    expect(all.d4).toBeDefined(); // nothing was lost to a write that started from an older copy of the map
    expect(all.d3.status).toBe("picked_up"); // d3 was "synced", not pending -> the write-back was correctly a no-op
  });

  it("keeps working after one queued write fails", async () => {
    await expect(queueStatusUpdate("never-cached", { status: "x" })).rejects.toThrow();
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1" }]);
    expect(await getCachedDelivery("d1")).not.toBeNull();
  });
});

describe("clearAll", () => {
  it("wipes all cached data for the active user", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1" }]);
    await clearAll();
    expect(await getCachedDeliveries()).toEqual([]);
  });
});

describe("blocked, retrying and conflicted records", () => {
  const notAssigned = classifyRecordError({ code: "not_assigned", error: "This delivery isn't assigned to you." });
  const busy = classifyRecordError({ code: "concurrent_update", error: "busy" });

  async function queued() {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up", version: 1 }]);
    return queueStatusUpdate("d1", { status: "delivered" });
  }

  it("a blocked record leaves the sync queue but stays cached, with its reason and data untouched", async () => {
    const saved = await queued();
    expect(await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision)).toMatchObject({ applied: true, blocked: true });

    expect(await getPendingDeliveries()).toEqual([]);
    expect(await getCachedDelivery("d1")).toMatchObject({ status: "delivered", sync_status: "blocked", sync_error: { code: "not_assigned" } });
  });

  it("does not block a record that was edited again while the request was in flight", async () => {
    const first = await queued();
    await queueStatusUpdate("d1", { status: "failed_attempt" });
    expect(await markBlockedIfUnchanged("d1", notAssigned, first.local_revision)).toEqual({ applied: false, reason: "changed_during_sync" });
    expect(await getPendingDeliveries()).toHaveLength(1);
  });

  it("a fresh edit of a blocked record clears the old error and queues it again", async () => {
    const saved = await queued();
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    const edited = await queueStatusUpdate("d1", { status: "failed_attempt" });
    expect(edited).toMatchObject({ sync_status: "pending" });
    expect(edited).not.toHaveProperty("sync_error");
  });

  it("retrying puts a blocked record back in the queue with a clean slate", async () => {
    const saved = await queued();
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    expect(await retryBlockedRecord("d1")).toMatchObject({ applied: true });
    expect(await getPendingDeliveries()).toHaveLength(1);
    expect(await getCachedDelivery("d1")).not.toHaveProperty("sync_error");
  });

  it("a routine server refresh does not erase a blocked record", async () => {
    const saved = await queued();
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up", version: 9 }]);
    expect(await getCachedDelivery("d1")).toMatchObject({ status: "delivered", sync_status: "blocked" });
  });

  it("discarding removes only the local copy", async () => {
    const saved = await queued();
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    await discardLocalRecord("d1");
    expect(await getCachedDelivery("d1")).toBeNull();
  });

  it(`retryable failures are counted and the record is blocked at attempt ${MAX_RECORD_ATTEMPTS}`, async () => {
    const saved = await queued();
    for (let attempt = 1; attempt < MAX_RECORD_ATTEMPTS; attempt += 1) {
      expect(await markRetryableFailureIfUnchanged("d1", busy, saved.local_revision)).toMatchObject({ applied: true, blocked: false });
      expect(await getCachedDelivery("d1")).toMatchObject({ sync_status: "pending", sync_attempts: attempt });
    }
    expect(await markRetryableFailureIfUnchanged("d1", busy, saved.local_revision)).toMatchObject({ applied: true, blocked: true });
    expect(await getPendingDeliveries()).toEqual([]);
    expect(await getCachedDelivery("d1")).toMatchObject({ sync_status: "blocked", sync_error: { code: "retry_exhausted" } });
  });

  it("stores the server's copy as conflicted; acknowledging makes it plain synced", async () => {
    const saved = await queued();
    const conflict = { id: "d1", order_id: "ORD-1", message: "Cancelled meanwhile.", your_status: "delivered", kept_status: "cancelled" };
    await markConflictedIfUnchanged("d1", { id: "d1", order_id: "ORD-1", status: "cancelled", version: 5 }, conflict, saved.local_revision);
    expect(await getCachedDelivery("d1")).toMatchObject({ status: "cancelled", sync_status: "conflicted", sync_conflict: { message: "Order ORD-1: Cancelled meanwhile." } });

    await acknowledgeConflict("d1");
    const after = await getCachedDelivery("d1");
    expect(after).toMatchObject({ status: "cancelled", sync_status: "synced" });
    expect(after).not.toHaveProperty("sync_conflict");
  });
});

describe("event_id — the idempotency key of each local edit", () => {
  it("every edit gets its own event_id, and 'try again' on a blocked record keeps the original one", async () => {
    await cacheDeliveries([{ id: "d1", order_id: "ORD-1", status: "picked_up", version: 1 }]);
    const first = await queueStatusUpdate("d1", { status: "out_for_delivery" });
    const second = await queueStatusUpdate("d1", { status: "delivered" });
    expect(first.event_id).toBeTruthy();
    expect(second.event_id).not.toBe(first.event_id);

    await markBlockedIfUnchanged("d1", classifyRecordError({ code: "not_assigned" }), second.local_revision);
    await retryBlockedRecord("d1");
    expect((await getCachedDelivery("d1")).event_id).toBe(second.event_id);
  });
});
