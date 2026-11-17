/**
 * Runs the REAL indexedDb.js against fake-indexeddb (an in-memory IndexedDB), so these tests
 * exercise the actual transactions — in particular the "agent edits again while a sync request
 * is in flight" race that used to silently lose the second edit.
 */
import "fake-indexeddb/auto";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../backgroundSync", () => ({ requestBackgroundSync: vi.fn() }));
import { requestBackgroundSync } from "../backgroundSync";

import {
  setActiveUser,
  getSyncLockName,
  saveDeliveryLocally,
  getAllLocalDeliveries,
  getPendingDeliveries,
  markAsSyncedIfUnchanged,
  storeServerRecord,
  mergeAssignedDeliveries,
  deleteDeliveryLocally,
  markBlockedIfUnchanged,
  markRetryableFailureIfUnchanged,
  markConflictedIfUnchanged,
  retryBlockedRecord,
  acknowledgeConflict,
} from "../indexedDb";
import { classifyRecordError, MAX_RECORD_ATTEMPTS } from "../syncOutcome";

let userCounter = 0;
beforeEach(() => {
  userCounter += 1;
  setActiveUser(`test-user-${userCounter}`); // a fresh database per test
});

const base = { id: "d1", order_id: "ORD-1", agent_id: "a1", status: "picked_up" };

describe("local revisions", () => {
  it("starts at 1 and increments on every local edit", async () => {
    expect((await saveDeliveryLocally(base)).local_revision).toBe(1);
    expect((await saveDeliveryLocally({ ...base, status: "out_for_delivery" })).local_revision).toBe(2);
    expect((await saveDeliveryLocally({ ...base, status: "delivered" })).local_revision).toBe(3);
  });

  it("takes the revision from what is STORED, not from the (possibly stale) object passed in", async () => {
    await saveDeliveryLocally(base);
    await saveDeliveryLocally(base);
    const saved = await saveDeliveryLocally({ ...base, local_revision: 1 }); // stale copy from React state
    expect(saved.local_revision).toBe(3);
  });

  it("gives two edits fired at the same time different revisions", async () => {
    const [a, b] = await Promise.all([
      saveDeliveryLocally({ ...base, status: "out_for_delivery" }),
      saveDeliveryLocally({ ...base, status: "delivered" }),
    ]);
    expect([a.local_revision, b.local_revision].sort()).toEqual([1, 2]);
  });

  it("scopes the cross-tab sync lock name to the signed-in user", () => {
    expect(getSyncLockName()).toBe(`delivery-sync:delivery_sync_db_test-user-${userCounter}`);
  });
});

describe("markAsSyncedIfUnchanged — the lost-update race", () => {
  it("applies the server's answer when nothing changed while the request was in flight", async () => {
    const sent = await saveDeliveryLocally(base); // revision 1, this is what gets uploaded

    const outcome = await markAsSyncedIfUnchanged("d1", { ...base, status: "picked_up", server_field: "x" }, sent.local_revision);

    expect(outcome).toEqual({ applied: true });
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ sync_status: "synced", server_field: "x", local_revision: 1 });
    expect(await getPendingDeliveries()).toHaveLength(0);
  });

  it("does NOT overwrite an edit the agent made while the earlier request was in flight", async () => {
    const sent = await saveDeliveryLocally({ ...base, status: "out_for_delivery" }); // revision 1 is uploaded...
    await saveDeliveryLocally({ ...base, status: "delivered" });                      // ...then the agent edits again (revision 2)

    // The response for revision 1 arrives late.
    const outcome = await markAsSyncedIfUnchanged("d1", { ...base, status: "out_for_delivery" }, sent.local_revision);

    expect(outcome).toEqual({ applied: false, reason: "changed_during_sync" });
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ status: "delivered", sync_status: "pending", local_revision: 2 }); // newer edit intact, still queued
    expect(await getPendingDeliveries()).toHaveLength(1);
  });

  it("keeps the revision counter going after a sync, so the next edit is never confused with an old one", async () => {
    const sent = await saveDeliveryLocally(base);
    await markAsSyncedIfUnchanged("d1", base, sent.local_revision);
    expect((await saveDeliveryLocally({ ...base, status: "delivered" })).local_revision).toBe(2);
  });

  it("does not resurrect a record that was deleted locally mid-flight", async () => {
    const sent = await saveDeliveryLocally(base);
    await deleteDeliveryLocally("d1");
    const outcome = await markAsSyncedIfUnchanged("d1", base, sent.local_revision);
    expect(outcome).toEqual({ applied: false, reason: "deleted_locally" });
    expect(await getAllLocalDeliveries()).toHaveLength(0);
  });
});

describe("pulling records from the server never clobbers a pending local edit", () => {
  it("stores new records and refreshes already-synced ones", async () => {
    await storeServerRecord("d1", base);
    await storeServerRecord("d1", { ...base, status: "delivered" });
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ status: "delivered", sync_status: "synced" });
  });

  it("skips a record the agent has edited but not yet synced", async () => {
    await saveDeliveryLocally({ ...base, status: "delivered" });
    await mergeAssignedDeliveries([{ ...base, status: "picked_up" }]);
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ status: "delivered", sync_status: "pending" });
  });
});

describe("blocked records (the dead-letter queue)", () => {
  const notAssigned = classifyRecordError({ code: "not_assigned", error: "This delivery isn't assigned to you." });

  it("a blocked record leaves the sync queue but stays stored, with its reason and its data untouched", async () => {
    const saved = await saveDeliveryLocally({ ...base, status: "delivered" });
    const result = await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);

    expect(result).toMatchObject({ applied: true, blocked: true });
    expect(await getPendingDeliveries()).toEqual([]); // never re-sent
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ id: "d1", status: "delivered", sync_status: "blocked", sync_error: { code: "not_assigned" } });
  });

  it("does NOT block a record the agent edited again while the request was in flight (the edit may fix it)", async () => {
    const first = await saveDeliveryLocally(base);
    await saveDeliveryLocally({ ...base, status: "delivered" }); // newer edit
    const result = await markBlockedIfUnchanged("d1", notAssigned, first.local_revision);

    expect(result).toEqual({ applied: false, reason: "changed_during_sync" });
    expect((await getPendingDeliveries())).toHaveLength(1);
  });

  it("a fresh edit of a blocked record clears the old error and queues it again", async () => {
    const saved = await saveDeliveryLocally(base);
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    const [blocked] = await getAllLocalDeliveries();

    const edited = await saveDeliveryLocally({ ...blocked, status: "delivered" }); // the stale React copy still carries sync_error
    expect(edited).toMatchObject({ sync_status: "pending", local_revision: 2 });
    expect(edited).not.toHaveProperty("sync_error");
    expect(await getPendingDeliveries()).toHaveLength(1);
  });

  it("retryBlockedRecord puts it back in the queue with a clean slate and asks for a background sync", async () => {
    const saved = await saveDeliveryLocally(base);
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    requestBackgroundSync.mockClear();

    expect(await retryBlockedRecord("d1")).toMatchObject({ applied: true });
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ sync_status: "pending" });
    expect(stored).not.toHaveProperty("sync_error");
    expect(requestBackgroundSync).toHaveBeenCalled();
    expect(await retryBlockedRecord("d1")).toEqual({ applied: false, reason: "not_blocked" }); // already pending
  });

  it("a routine pull from the server does not erase a blocked record", async () => {
    const saved = await saveDeliveryLocally({ ...base, status: "delivered" });
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    await mergeAssignedDeliveries([{ ...base, status: "picked_up" }]);

    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ status: "delivered", sync_status: "blocked" });
  });

  it("discarding removes only the local copy", async () => {
    const saved = await saveDeliveryLocally(base);
    await markBlockedIfUnchanged("d1", notAssigned, saved.local_revision);
    await deleteDeliveryLocally("d1");
    expect(await getAllLocalDeliveries()).toEqual([]);
  });
});

describe("retryable failures are counted, then blocked — never an endless loop", () => {
  const busy = classifyRecordError({ code: "concurrent_update", error: "busy" });

  it(`stays pending (with a visible counter) until attempt ${MAX_RECORD_ATTEMPTS}, then is blocked`, async () => {
    const saved = await saveDeliveryLocally(base);
    for (let attempt = 1; attempt < MAX_RECORD_ATTEMPTS; attempt += 1) {
      const result = await markRetryableFailureIfUnchanged("d1", busy, saved.local_revision);
      expect(result).toMatchObject({ applied: true, blocked: false });
      const [stored] = await getAllLocalDeliveries();
      expect(stored).toMatchObject({ sync_status: "pending", sync_attempts: attempt });
    }
    const last = await markRetryableFailureIfUnchanged("d1", busy, saved.local_revision);
    expect(last).toMatchObject({ applied: true, blocked: true });
    expect(await getPendingDeliveries()).toEqual([]);
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ sync_status: "blocked", sync_error: { code: "retry_exhausted" } });
  });
});

describe("conflicted records", () => {
  const conflict = { id: "d1", order_id: "ORD-1", code: "stale_version", message: "Cancelled after you last synced.", your_status: "delivered", kept_status: "cancelled" };

  it("stores the server's copy flagged conflicted with the explanation; acknowledging makes it plain synced", async () => {
    const saved = await saveDeliveryLocally({ ...base, status: "delivered" });
    const result = await markConflictedIfUnchanged("d1", { ...base, status: "cancelled", version: 5 }, conflict, saved.local_revision);
    expect(result.applied).toBe(true);
    expect(await getPendingDeliveries()).toEqual([]);
    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ status: "cancelled", version: 5, sync_status: "conflicted", sync_conflict: { message: "Order ORD-1: Cancelled after you last synced." } });

    await acknowledgeConflict("d1");
    const [after] = await getAllLocalDeliveries();
    expect(after).toMatchObject({ status: "cancelled", sync_status: "synced" });
    expect(after).not.toHaveProperty("sync_conflict");
  });

  it("a pull refreshes a conflicted record's data but keeps the notice until it is acknowledged", async () => {
    const saved = await saveDeliveryLocally({ ...base, status: "delivered" });
    await markConflictedIfUnchanged("d1", { ...base, status: "cancelled", version: 5 }, conflict, saved.local_revision);
    await mergeAssignedDeliveries([{ ...base, status: "cancelled", version: 6 }]);

    const [stored] = await getAllLocalDeliveries();
    expect(stored).toMatchObject({ version: 6, sync_status: "conflicted", sync_conflict: { message: expect.any(String) } });
  });
});

describe("event_id — the idempotency key of each local edit", () => {
  it("every edit gets its own event_id, and 'try again' on a blocked record keeps the original one", async () => {
    const first = await saveDeliveryLocally(base);
    expect(first.event_id).toBeTruthy();
    const second = await saveDeliveryLocally({ ...base, status: "delivered" });
    expect(second.event_id).toBeTruthy();
    expect(second.event_id).not.toBe(first.event_id);          // a new edit is a new event

    await markBlockedIfUnchanged("d1", classifyRecordError({ code: "not_assigned" }), second.local_revision);
    await retryBlockedRecord("d1");
    const [retried] = await getAllLocalDeliveries();
    expect(retried.event_id).toBe(second.event_id);            // the SAME edit is retried: same key, so it can't be applied twice
  });
});
