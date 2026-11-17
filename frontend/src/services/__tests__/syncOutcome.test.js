import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, it, expect } from "vitest";
import {
  MAX_RECORD_ATTEMPTS,
  classifyRecordError,
  classifyRequestError,
  decideOutcome,
  summarizeRunResult,
  summarizeBackgroundSync,
  extractRecordFaults,
  formatServerDetail,
  toSyncPayload,
  chunk,
  effectiveSyncState,
  summarizeSyncStates,
  describeSyncProblem,
  describeConflict,
  decideBlockedWrite,
  decideRetryableFailureWrite,
  decideConflictWrite,
  decideRetryBlocked,
  decideAcknowledgeConflict,
  withoutLocalSyncFields,
  newEventId,
} from "../syncOutcome";
import { decideServerRecordWrite } from "../syncRevision";

const pending = (extra = {}) => ({ id: "1", order_id: "ORD-1", status: "delivered", version: 3, local_revision: 2, sync_status: "pending", ...extra });

describe("classifyRecordError", () => {

  const PERMANENT = ["not_assigned", "identity_mismatch", "not_found", "invalid_agent", "invalid_record", "base_version_required"];
  for (const code of PERMANENT) {
    it(`${code} is permanent and offers a way out (discard)`, () => {
      const info = classifyRecordError({ id: "1", error: "server words", code });
      expect(info).toMatchObject({ code, kind: "permanent", message: "server words" });
      expect(info.actions).toContain("discard");
      expect(info.title).toBeTruthy();
      expect(info.hint).toBeTruthy();
    });
  }

  it("concurrent_update is retryable", () => {
    expect(classifyRecordError({ code: "concurrent_update", error: "x" })).toMatchObject({ kind: "retryable", actions: [] });
  });

  it("unknown or missing codes are retryable (bounded by attempts) rather than ignored", () => {
    expect(classifyRecordError({ code: "something_new" })).toMatchObject({ kind: "retryable", code: "something_new" });
    expect(classifyRecordError({ message: "oops" })).toMatchObject({ kind: "retryable", code: "unknown_error", message: "oops" });
    expect(classifyRecordError(undefined)).toMatchObject({ kind: "retryable", code: "unknown_error" });
  });

  it("retry_exhausted offers both retry and discard", () => {
    expect(classifyRecordError({ code: "retry_exhausted" }).actions).toEqual(["retry", "discard"]);
  });
});

describe("waiting for proof of delivery (pod_required)", () => {
  const waiting = classifyRecordError({ code: "pod_required", error: "Proof of delivery is required before this delivery can be marked as delivered." });

  it("is retryable and marked as waiting, so it is explained rather than blocked", () => {
    expect(waiting).toMatchObject({ kind: "retryable", waiting: true, title: "Waiting for proof of delivery", actions: [] });
  });

  it("never uses up retry attempts, however long the proof of delivery takes to arrive", () => {
    let record = pending();
    for (let i = 0; i < MAX_RECORD_ATTEMPTS * 3; i += 1) {
      const d = decideRetryableFailureWrite(record, waiting, 2);
      expect(d.blocked).toBe(false);
      record = d.record;
    }
    expect(record.sync_status).toBe("pending");
    expect(record.sync_attempts).toBeUndefined();
    expect(record.sync_error.code).toBe("pod_required");
  });

  it("is described as waiting (not as 'attempt n of 5')", () => {
    const problem = describeSyncProblem(decideRetryableFailureWrite(pending(), waiting, 2).record);
    expect(problem).toMatchObject({ state: "pending", title: "Waiting for proof of delivery" });
    expect(problem.detail).not.toContain("attempt");
  });

  it("ordinary retryable errors are not marked as waiting", () => {
    expect(classifyRecordError({ code: "concurrent_update" }).waiting).toBe(false);
    expect(classifyRecordError({ code: "server_error" })).toMatchObject({ kind: "retryable", waiting: false });
  });
});

describe("newEventId — the idempotency key of one local edit", () => {
  it("is unique per call and matches what the server accepts (8-128 chars of [A-Za-z0-9._:-])", () => {
    const ids = new Set(Array.from({ length: 500 }, () => newEventId()));
    expect(ids.size).toBe(500);
    for (const id of ids) expect(/^[A-Za-z0-9._:-]{8,128}$/.test(id)).toBe(true);
  });

  it("works without crypto.randomUUID (older WebViews, React Native)", () => {
    const original = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", { value: undefined, configurable: true });
    try {
      const id = newEventId();
      expect(/^[A-Za-z0-9._:-]{8,128}$/.test(id)).toBe(true);
    } finally {
      Object.defineProperty(globalThis, "crypto", { value: original, configurable: true });
    }
  });

  it("is carried to the server with the record and survives a blocked -> retry round trip unchanged", () => {
    const record = pending({ event_id: "evt-1234567890" });
    expect(toSyncPayload(record).event_id).toBe("evt-1234567890");
    const blocked = decideBlockedWrite(record, classifyRecordError({ code: "not_assigned" }), 2).record;
    expect(decideRetryBlocked(blocked).record.event_id).toBe("evt-1234567890");
  });
});

describe("classifyRequestError", () => {
  it("401/403 are auth problems (nobody is blamed)", () => {
    expect(classifyRequestError({ status: 401 }).scope).toBe("auth");
    expect(classifyRequestError({ status: 403 }).scope).toBe("auth");
  });
  it("no status or transient statuses are transport problems", () => {
    for (const status of [undefined, 408, 425, 429, 502, 503, 504]) expect(classifyRequestError({ status }).scope).toBe("transport");
    expect(classifyRequestError(new TypeError("Failed to fetch")).scope).toBe("transport");
  });
  it("400/413/422 mean the payload is at fault: some record must be isolated", () => {
    for (const status of [400, 413, 422]) expect(classifyRequestError({ status }).scope).toBe("rejected");
  });
  it("404/500 and friends are outages or misconfiguration — never blamed on a record", () => {
    for (const status of [404, 405, 409, 500, 501]) expect(classifyRequestError({ status }).scope).toBe("server");
  });
});

describe("decideOutcome", () => {
  it("success when nothing needs attention, including an empty queue", () => {
    expect(decideOutcome({})).toBe("success");
    expect(decideOutcome({ syncedCount: 5 })).toBe("success");
  });
  it("partial_success when something settled but something still needs attention", () => {
    expect(decideOutcome({ syncedCount: 2, blockedCount: 1 })).toBe("partial_success");
    expect(decideOutcome({ syncedCount: 1, retryingCount: 1 })).toBe("partial_success");
    expect(decideOutcome({ syncedCount: 1, requestFailed: true })).toBe("partial_success");
  });
  it("a server-resolved conflict is never plain success", () => {
    expect(decideOutcome({ conflictCount: 1 })).toBe("partial_success");
  });
  it("failed when nothing settled and something needs attention", () => {
    expect(decideOutcome({ blockedCount: 2 })).toBe("failed");
    expect(decideOutcome({ retryingCount: 1 })).toBe("failed");
    expect(decideOutcome({ requestFailed: true })).toBe("failed");
  });
});

describe("summarizeRunResult — honest wording for every outcome", () => {
  it("success: says how many synced, or that it was already up to date", () => {
    expect(summarizeRunResult({ outcome: "success", syncedCount: 1 })).toEqual({ tone: "success", message: "Synced 1 record." });
    expect(summarizeRunResult({ outcome: "success", syncedCount: 3 }).message).toBe("Synced 3 records.");
    expect(summarizeRunResult({ outcome: "success", syncedCount: 0 })).toEqual({ tone: "info", message: "Already up to date." });
  });
  it("a skipped run says another sync is running", () => {
    expect(summarizeRunResult({ outcome: "skipped", skipped: true }).tone).toBe("info");
  });
  it("partial_success never claims everything synced", () => {
    const r = summarizeRunResult({ outcome: "partial_success", syncedCount: 2, blockedCount: 1 });
    expect(r.tone).toBe("error");
    expect(r.message).toBe("Synced 2. 1 record needs attention — see Sync status below.");
  });
  it("failed with every record refused points at the Sync status panel", () => {
    const r = summarizeRunResult({ outcome: "failed", syncedCount: 0, blockedCount: 2 });
    expect(r.message).toBe("Nothing could be synced. 2 records need attention — see Sync status below.");
  });
  it("a network/server failure says the work is safe and will be retried", () => {
    const r = summarizeRunResult({ outcome: "failed", requestFailed: true, error: "boom" });
    expect(r.message).toContain("saved on this device");
    expect(r.message).toContain("retried automatically");
  });
  it("an expired session says to sign in", () => {
    expect(summarizeRunResult({ outcome: "failed", requestFailed: true, authError: true }).message).toContain("sign in again");
  });
  it("progress before a connection failure is still reported", () => {
    expect(summarizeRunResult({ outcome: "partial_success", requestFailed: true, syncedCount: 4 }).message).toContain("Synced 4, then the connection failed");
  });
});

describe("summarizeBackgroundSync", () => {
  it("is quiet when nothing happened", () => {
    expect(summarizeBackgroundSync({})).toBeNull();
    expect(summarizeBackgroundSync(undefined)).toBeNull();
  });
  it("reports synced, refused and to-be-retried items in one honest sentence", () => {
    expect(summarizeBackgroundSync({ syncedCount: 3 })).toEqual({ tone: "info", message: "Synced 3 queued items." });
    const r = summarizeBackgroundSync({ syncedCount: 1, blockedCount: 2, retryCount: 1 });
    expect(r.tone).toBe("error");
    expect(r.message).toBe("Synced 1 queued item; 2 were refused by the server and can't sync; 1 will be retried automatically.");
  });
  it("tells the user to sign in when the session expired", () => {
    expect(summarizeBackgroundSync({ authExpired: true })).toEqual({ tone: "error", message: "Sign in again to sync the rest." });
  });
});

describe("server error detail helpers", () => {
  it("formatServerDetail handles strings, FastAPI lists and garbage", () => {
    expect(formatServerDetail("nope")).toBe("nope");
    expect(formatServerDetail([{ loc: ["body", "records", 2, "status"], msg: "bad status" }])).toBe("status: bad status");
    expect(formatServerDetail([{ loc: ["body"], msg: "required" }, { msg: "other" }])).toBe("required; other");
    expect(formatServerDetail(undefined)).toBeNull();
    expect(formatServerDetail({})).toBeNull();
  });

  it("extractRecordFaults finds the offending records by position and ignores everything else", () => {
    const detail = [
      { loc: ["body", "records", 1, "status"], msg: "bad status" },
      { loc: ["body", "records", 1, "id"], msg: "required" },
      { loc: ["body", "records", 4, "x"], msg: "y" },
      { loc: ["body", "records", 99, "x"], msg: "out of range" },
      { loc: ["query", "limit"], msg: "unrelated" },
    ];
    expect(extractRecordFaults(detail, 5)).toEqual([
      { index: 1, message: "status: bad status; id: required" },
      { index: 4, message: "x: y" },
    ]);
    expect(extractRecordFaults("a string", 5)).toEqual([]);
  });
});

describe("payload and states", () => {
  it("toSyncPayload strips device-only bookkeeping and adds revision / base version", () => {
    const payload = toSyncPayload(pending({ sync_error: { code: "x" }, sync_attempts: 2, sync_conflict: { a: 1 } }));
    expect(payload).toMatchObject({ id: "1", local_revision: 2, base_version: 3 });
    expect(payload).not.toHaveProperty("sync_error");
    expect(payload).not.toHaveProperty("sync_attempts");
    expect(payload).not.toHaveProperty("sync_conflict");
    expect(toSyncPayload({ id: "9" })).toMatchObject({ local_revision: 0, base_version: 0 });
  });

  it("chunk splits into groups of at most `size`", () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
    expect(chunk([], 3)).toEqual([]);
  });

  it("effectiveSyncState shows 'syncing' only for pending records that are in flight", () => {
    const flying = new Set(["1"]);
    expect(effectiveSyncState({ id: "1", sync_status: "pending" }, flying)).toBe("syncing");
    expect(effectiveSyncState({ id: "2", sync_status: "pending" }, flying)).toBe("pending");
    expect(effectiveSyncState({ id: "1", sync_status: "blocked" }, flying)).toBe("blocked");
    expect(effectiveSyncState({ id: "3" }, flying)).toBe("synced"); // server-fetched records carry no marker
  });

  it("summarizeSyncStates counts every state and what needs attention", () => {
    const records = [
      { id: "1", sync_status: "pending" },
      { id: "2", sync_status: "pending", sync_error: { kind: "retryable" } },
      { id: "3", sync_status: "synced" },
      { id: "4", sync_status: "blocked" },
      { id: "5", sync_status: "conflicted" },
      { id: "6" },
    ];
    expect(summarizeSyncStates(records, new Set(["1"]))).toEqual({
      pending: 1, syncing: 1, synced: 2, conflicted: 1, blocked: 1, total: 6, retrying: 1, needsAttention: 3,
    });
    expect(summarizeSyncStates([])).toMatchObject({ total: 0, needsAttention: 0 });
  });
});

describe("describeSyncProblem — every non-trivial state has an explanation", () => {
  it("returns null for healthy and plainly-waiting records", () => {
    expect(describeSyncProblem({ sync_status: "synced" })).toBeNull();
    expect(describeSyncProblem({ sync_status: "pending" })).toBeNull();
    expect(describeSyncProblem(null)).toBeNull();
  });

  it("blocked: reason, hint and recovery actions", () => {
    const record = { sync_status: "blocked", sync_error: classifyRecordError({ code: "not_assigned", error: "This delivery isn't assigned to you." }) };
    const problem = describeSyncProblem(record);
    expect(problem).toMatchObject({ state: "blocked", tone: "danger", detail: "This delivery isn't assigned to you." });
    expect(problem.actions.map((a) => a.id)).toEqual(["discard"]);
  });

  it("blocked after retries: offers try again as well as discard", () => {
    const problem = describeSyncProblem({ sync_status: "blocked", sync_error: classifyRecordError({ code: "retry_exhausted", message: "boom" }) });
    expect(problem.actions.map((a) => a.id)).toEqual(["retry", "discard"]);
  });

  it("conflicted: shows what happened and a way to dismiss it", () => {
    const problem = describeSyncProblem({ sync_status: "conflicted", sync_conflict: { message: "Order ORD-1: cancelled meanwhile." } });
    expect(problem).toMatchObject({ state: "conflicted", detail: "Order ORD-1: cancelled meanwhile." });
    expect(problem.actions.map((a) => a.id)).toEqual(["acknowledge"]);
  });

  it("pending with a retryable error: says it will retry, with the attempt counter", () => {
    const problem = describeSyncProblem({ sync_status: "pending", sync_attempts: 2, sync_error: classifyRecordError({ code: "concurrent_update", error: "busy" }) });
    expect(problem).toMatchObject({ state: "pending", title: "Will retry automatically" });
    expect(problem.detail).toBe(`busy (attempt 2 of ${MAX_RECORD_ATTEMPTS})`);
  });
});

describe("state transitions (all guarded against newer local edits)", () => {
  const notAssigned = classifyRecordError({ code: "not_assigned", error: "no" });
  const retryable = classifyRecordError({ code: "concurrent_update", error: "busy" });

  it("decideBlockedWrite quarantines the record with its reason", () => {
    const d = decideBlockedWrite(pending(), notAssigned, 2);
    expect(d.write).toBe(true);
    expect(d.record).toMatchObject({ id: "1", status: "delivered", sync_status: "blocked", sync_error: { code: "not_assigned" } });
    expect(d.record.sync_error.at).toBeTruthy();
  });

  it("decideBlockedWrite leaves a record alone if it changed meanwhile, was deleted, or is already settled", () => {
    expect(decideBlockedWrite(pending({ local_revision: 3 }), notAssigned, 2)).toEqual({ write: false, reason: "changed_during_sync" });
    expect(decideBlockedWrite(undefined, notAssigned, 2)).toEqual({ write: false, reason: "deleted_locally" });
    expect(decideBlockedWrite(pending({ sync_status: "synced" }), notAssigned, 2)).toEqual({ write: false, reason: "already_settled" });
  });

  it("decideRetryableFailureWrite counts attempts and keeps the record pending", () => {
    const first = decideRetryableFailureWrite(pending(), retryable, 2);
    expect(first.record).toMatchObject({ sync_status: "pending", sync_attempts: 1, sync_error: { code: "concurrent_update", kind: "retryable" } });
    expect(first.blocked).toBe(false);
    const second = decideRetryableFailureWrite(first.record, retryable, 2);
    expect(second.record.sync_attempts).toBe(2);
  });

  it(`decideRetryableFailureWrite blocks the record at attempt ${MAX_RECORD_ATTEMPTS}, so nothing can retry forever`, () => {
    const d = decideRetryableFailureWrite(pending({ sync_attempts: MAX_RECORD_ATTEMPTS - 1 }), retryable, 2);
    expect(d.blocked).toBe(true);
    expect(d.record).toMatchObject({ sync_status: "blocked", sync_attempts: MAX_RECORD_ATTEMPTS, sync_error: { code: "retry_exhausted", actions: ["retry", "discard"] } });
    expect(d.record.sync_error.message).toContain("busy");
  });

  it("decideRetryableFailureWrite respects the same guards", () => {
    expect(decideRetryableFailureWrite(pending({ local_revision: 9 }), retryable, 2).write).toBe(false);
  });

  it("decideConflictWrite stores the server's copy flagged conflicted, with the explanation", () => {
    const conflict = { id: "1", order_id: "ORD-1", code: "stale_version", message: "Cancelled after you last synced.", your_status: "delivered", kept_status: "cancelled" };
    const d = decideConflictWrite(pending({ sync_error: { code: "x" }, sync_attempts: 1 }), { id: "1", order_id: "ORD-1", status: "cancelled", version: 7 }, conflict, 2);
    expect(d.write).toBe(true);
    expect(d.record).toMatchObject({
      status: "cancelled", version: 7, sync_status: "conflicted", local_revision: 2,
      sync_conflict: { code: "stale_version", message: "Order ORD-1: Cancelled after you last synced.", your_status: "delivered", kept_status: "cancelled" },
    });
    expect(d.record).not.toHaveProperty("sync_error");
    expect(decideConflictWrite(pending({ local_revision: 5 }), {}, conflict, 2).write).toBe(false);
  });

  it("decideRetryBlocked puts a blocked record back in the queue with a clean slate", () => {
    const blocked = decideBlockedWrite(pending({ sync_attempts: 4 }), notAssigned, 2).record;
    const d = decideRetryBlocked(blocked);
    expect(d.record).toMatchObject({ id: "1", status: "delivered", sync_status: "pending", local_revision: 2 });
    expect(d.record).not.toHaveProperty("sync_error");
    expect(d.record).not.toHaveProperty("sync_attempts");
    expect(decideRetryBlocked(pending())).toEqual({ write: false, reason: "not_blocked" });
    expect(decideRetryBlocked(undefined)).toEqual({ write: false, reason: "deleted_locally" });
  });

  it("decideAcknowledgeConflict turns a seen conflict into plain synced", () => {
    const d = decideAcknowledgeConflict({ id: "1", sync_status: "conflicted", sync_conflict: { message: "m" } });
    expect(d.record).toEqual({ id: "1", sync_status: "synced" });
    expect(decideAcknowledgeConflict(pending())).toEqual({ write: false, reason: "not_conflicted" });
  });

  it("withoutLocalSyncFields removes only the device-only fields", () => {
    expect(withoutLocalSyncFields({ id: "1", sync_error: 1, sync_attempts: 2, sync_conflict: 3, sync_status: "pending" })).toEqual({ id: "1", sync_status: "pending" });
  });
});

describe("a routine server pull never destroys unsent or unseen work", () => {
  const serverCopy = { id: "1", status: "assigned", version: 9 };

  it("does not overwrite a blocked record (the agent's change is kept until they choose retry or discard)", () => {
    expect(decideServerRecordWrite({ id: "1", sync_status: "blocked" }, serverCopy)).toEqual({ write: false, reason: "local_edit_blocked" });
  });

  it("refreshes a conflicted record's data but keeps the conflict notice", () => {
    const d = decideServerRecordWrite({ id: "1", sync_status: "conflicted", sync_conflict: { message: "m" }, local_revision: 4 }, serverCopy);
    expect(d.write).toBe(true);
    expect(d.record).toMatchObject({ status: "assigned", version: 9, sync_status: "conflicted", sync_conflict: { message: "m" }, local_revision: 4 });
  });

  it("still protects pending records and still refreshes synced ones", () => {
    expect(decideServerRecordWrite({ id: "1", sync_status: "pending" }, serverCopy).write).toBe(false);
    expect(decideServerRecordWrite({ id: "1", sync_status: "synced" }, serverCopy).record.sync_status).toBe("synced");
  });
});

describe("describeConflict", () => {
  it("prefers the server's own explanation", () => {
    expect(describeConflict({ order_id: "ORD-1", message: "Because." })).toBe("Order ORD-1: Because.");
  });
});

// ------------------------------------------------------------------------------------------------
// The service worker keeps its own copy of these rules (it can't import app modules). Prove the two
// copies agree, input for input, so they can't silently drift apart.
// ------------------------------------------------------------------------------------------------
describe("service worker parity (public/sw.js mirrors this module)", () => {
  const sandbox = { self: { addEventListener() {}, navigator: {} }, console, Date, Map, Set, Promise, JSON, Number, Array, Object, Error, String, Math };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.resolve(process.cwd(), "public/sw.js"), "utf8"), sandbox);
  const sw = (name) => vm.runInContext(name, sandbox);
  const normalize = (value) => JSON.parse(JSON.stringify(value, (key, v) => (key === "at" ? "<time>" : v)));

  it("uses the same limits", () => {
    expect(sw("SW_MAX_RECORD_ATTEMPTS")).toBe(MAX_RECORD_ATTEMPTS);
  });

  it("classifies record errors identically", () => {
    const inputs = [
      ...["not_assigned", "identity_mismatch", "not_found", "invalid_agent", "invalid_record", "base_version_required", "rejected_by_server", "retry_exhausted", "concurrent_update", "server_error", "pod_required", "brand_new_code"].map((code) => ({ code, error: "text" })),
      { code: "not_assigned" }, { message: "only a message" }, {}, undefined, null,
    ];
    for (const input of inputs) expect(normalize(sw("swClassifyRecordError")(input))).toEqual(normalize(classifyRecordError(input)));
  });

  it("classifies request failures identically", () => {
    for (const status of [undefined, 0, 200, 400, 401, 403, 404, 408, 413, 422, 425, 429, 500, 502, 503, 504]) {
      expect(normalize(sw("swClassifyRequestError")({ status }))).toEqual(normalize(classifyRequestError({ status })));
    }
  });

  it("formats details, finds faulty records and chunks identically", () => {
    const details = ["nope", [{ loc: ["body", "records", 2, "status"], msg: "bad" }, { loc: ["body", "records", 7, "id"], msg: "req" }], [{ msg: "x" }], undefined, {}, 5];
    for (const d of details) {
      expect(normalize(sw("swFormatServerDetail")(d))).toEqual(normalize(formatServerDetail(d)));
      expect(normalize(sw("swExtractRecordFaults")(d, 5))).toEqual(normalize(extractRecordFaults(d, 5)));
    }
    expect(normalize(sw("swChunk")([1, 2, 3, 4, 5], 2))).toEqual(chunk([1, 2, 3, 4, 5], 2));
  });

  it("makes identical state-transition decisions", () => {
    const info = classifyRecordError({ code: "concurrent_update", error: "busy" });
    const perm = classifyRecordError({ code: "not_assigned", error: "no" });
    const conflict = { id: "1", order_id: "ORD-1", code: "stale_version", message: "Cancelled.", your_status: "a", kept_status: "b" };
    const resolved = { id: "1", status: "cancelled", version: 7 };
    const waiting = classifyRecordError({ code: "pod_required", error: "wait" });
    for (const record of [pending(), pending({ sync_attempts: 3 })]) {
      expect(normalize(sw("swDecideRetryableFailureWrite")(record, waiting, 2))).toEqual(normalize(decideRetryableFailureWrite(record, waiting, 2)));
    }
    const records = [
      pending(), pending({ sync_attempts: MAX_RECORD_ATTEMPTS - 1 }), pending({ sync_attempts: 2, sync_error: { code: "x" } }),
      pending({ local_revision: 9 }), pending({ sync_status: "synced" }), pending({ sync_status: "blocked" }), undefined,
    ];
    for (const record of records) {
      expect(normalize(sw("swDecideBlockedWrite")(record, perm, 2))).toEqual(normalize(decideBlockedWrite(record, perm, 2)));
      expect(normalize(sw("swDecideRetryableFailureWrite")(record, info, 2))).toEqual(normalize(decideRetryableFailureWrite(record, info, 2)));
      expect(normalize(sw("swDecideConflictWrite")(record, resolved, conflict, 2))).toEqual(normalize(decideConflictWrite(record, resolved, conflict, 2)));
    }
  });
});
