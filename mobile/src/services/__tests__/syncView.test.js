import { mergeLocalSyncState } from "../syncView";

describe("mergeLocalSyncState", () => {
  const server = [{ id: "1", status: "picked_up" }, { id: "2", status: "picked_up" }];

  it("shows the server's copy when the local one is plainly synced (or absent)", () => {
    const local = [{ id: "1", status: "stale", sync_status: "synced" }];
    expect(mergeLocalSyncState(server, local)).toEqual(server);
  });

  it("shows the LOCAL copy of a pending, blocked or conflicted record, so the agent sees their own state", () => {
    const local = [
      { id: "1", status: "delivered", sync_status: "pending" },
      { id: "2", status: "delivered", sync_status: "blocked" },
    ];
    expect(mergeLocalSyncState(server, local).map((r) => r.sync_status)).toEqual(["pending", "blocked"]);
  });

  it("still lists a record that needs attention even though the server no longer lists it", () => {
    const local = [{ id: "9", status: "delivered", sync_status: "blocked" }, { id: "8", status: "old", sync_status: "synced" }];
    const merged = mergeLocalSyncState(server, local);
    expect(merged.map((r) => r.id)).toEqual(["1", "2", "9"]); // the stale synced one stays hidden
  });

  it("copes with empty inputs", () => {
    expect(mergeLocalSyncState([], [])).toEqual([]);
    expect(mergeLocalSyncState(undefined, undefined)).toEqual([]);
  });
});
