import { describe, it, expect } from "vitest";
import { revisionOf, nextRevision, decideSyncedWrite, decideServerRecordWrite } from "../syncRevision";

describe("revisions", () => {
  it("treats missing or invalid revisions as 0 and increments from the stored value", () => {
    expect(revisionOf(undefined)).toBe(0);
    expect(revisionOf({})).toBe(0);
    expect(revisionOf({ local_revision: -3 })).toBe(0);
    expect(revisionOf({ local_revision: "2" })).toBe(0);
    expect(nextRevision(undefined)).toBe(1);
    expect(nextRevision({ local_revision: 4 })).toBe(5);
  });
});

describe("decideSyncedWrite (the lost-update guard)", () => {
  const server = { id: "1", status: "delivered", updated_at: "2026-10-18T10:00:00Z" };

  it("writes the server's answer when the record is unchanged since it was sent", () => {
    const decision = decideSyncedWrite({ id: "1", sync_status: "pending", local_revision: 3 }, server, 3);
    expect(decision.write).toBe(true);
    expect(decision.record).toEqual({ ...server, sync_status: "synced", local_revision: 3 });
  });

  it("does NOT overwrite a record the agent edited again while the request was in flight", () => {
    const decision = decideSyncedWrite({ id: "1", sync_status: "pending", local_revision: 4, status: "failed_attempt" }, server, 3);
    expect(decision).toEqual({ write: false, reason: "changed_during_sync" });
  });

  it("does not resurrect a record deleted locally mid-flight", () => {
    expect(decideSyncedWrite(undefined, server, 1)).toEqual({ write: false, reason: "deleted_locally" });
  });

  it("leaves a record alone if something else already settled it", () => {
    expect(decideSyncedWrite({ id: "1", sync_status: "synced", local_revision: 3 }, server, 3))
      .toEqual({ write: false, reason: "already_synced" });
  });

  it("treats records that predate revisions (no field) as revision 0 on both sides", () => {
    expect(decideSyncedWrite({ id: "1", sync_status: "pending" }, server, 0).write).toBe(true);
  });
});

describe("decideServerRecordWrite", () => {
  const server = { id: "1", status: "picked_up" };

  it("stores a brand-new server record as synced", () => {
    expect(decideServerRecordWrite(undefined, server)).toEqual({ write: true, record: { ...server, sync_status: "synced", local_revision: 0 } });
  });

  it("replaces an already-synced local copy but keeps its revision counter", () => {
    const decision = decideServerRecordWrite({ id: "1", sync_status: "synced", local_revision: 5 }, server);
    expect(decision.record.local_revision).toBe(5);
  });

  it("never replaces an unsynced local edit", () => {
    expect(decideServerRecordWrite({ id: "1", sync_status: "pending", local_revision: 2 }, server))
      .toEqual({ write: false, reason: "local_edit_pending" });
  });
});
