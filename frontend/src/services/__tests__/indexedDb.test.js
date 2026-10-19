/**
 * Runs the REAL indexedDb.js against fake-indexeddb (an in-memory IndexedDB), so these tests
 * exercise the actual transactions — in particular the "agent edits again while a sync request
 * is in flight" race that used to silently lose the second edit.
 */
import "fake-indexeddb/auto";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../backgroundSync", () => ({ requestBackgroundSync: vi.fn() }));

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
} from "../indexedDb";

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
