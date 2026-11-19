/**
 * The mobile app keeps its own copy of the web app's sync rules (separate packages can't import each
 * other). This guarantees the two copies stay identical, and spot-checks the rules the mobile screens rely on.
 */
import fs from "fs";
import path from "path";
import {
  classifyRecordError,
  classifyRequestError,
  decideOutcome,
  summarizeSyncStates,
  describeSyncProblem,
  decideBlockedWrite,
  decideRetryableFailureWrite,
  MAX_RECORD_ATTEMPTS,
} from "../syncOutcome";

// Everything from the first code line on (the files differ only in their leading doc comment).
function codeWithoutHeader(file) {
  const source = fs.readFileSync(file, "utf8");
  return source.slice(source.indexOf("*/") + 2);
}

describe("mobile syncOutcome.js is an exact copy of the web one (header comment aside)", () => {
  it("has identical code", () => {
    const web = path.resolve(process.cwd(), "../frontend/src/services/syncOutcome.js");
    const mobile = path.resolve(process.cwd(), "src/services/syncOutcome.js");
    expect(codeWithoutHeader(mobile)).toBe(codeWithoutHeader(web));
  });

  it("syncRevision.js copies have identical code too", () => {
    const strip = (file) => {
      const source = fs.readFileSync(file, "utf8");
      return source.slice(source.indexOf("*/") + 2);
    };
    const web = path.resolve(process.cwd(), "../frontend/src/services/syncRevision.js");
    const mobile = path.resolve(process.cwd(), "src/services/syncRevision.js");
    expect(strip(mobile)).toBe(strip(web));
  });
});

describe("the rules the mobile screens rely on", () => {
  it("classifies permanent and retryable record errors", () => {
    expect(classifyRecordError({ code: "not_assigned", error: "x" })).toMatchObject({ kind: "permanent", actions: ["discard"] });
    expect(classifyRecordError({ code: "concurrent_update" }).kind).toBe("retryable");
    expect(classifyRecordError({ code: "never_seen_before" }).kind).toBe("retryable");
  });

  it("classifies failed requests", () => {
    expect(classifyRequestError({ status: 401 }).scope).toBe("auth");
    expect(classifyRequestError({}).scope).toBe("transport");
    expect(classifyRequestError({ status: 422 }).scope).toBe("rejected");
    expect(classifyRequestError({ status: 404 }).scope).toBe("server");
  });

  it("only reports success when nothing needs attention", () => {
    expect(decideOutcome({ syncedCount: 3 })).toBe("success");
    expect(decideOutcome({ syncedCount: 3, blockedCount: 1 })).toBe("partial_success");
    expect(decideOutcome({ blockedCount: 1 })).toBe("failed");
  });

  it("counts every state and explains a blocked record", () => {
    const records = [{ id: "1", sync_status: "pending" }, { id: "2", sync_status: "blocked", sync_error: classifyRecordError({ code: "not_found", error: "gone" }) }];
    expect(summarizeSyncStates(records, new Set(["1"]))).toMatchObject({ syncing: 1, blocked: 1, needsAttention: 1 });
    expect(describeSyncProblem(records[1])).toMatchObject({ state: "blocked", detail: "gone" });
  });

  it("blocks after the maximum number of attempts", () => {
    const record = { id: "1", local_revision: 1, sync_status: "pending", sync_attempts: MAX_RECORD_ATTEMPTS - 1 };
    expect(decideRetryableFailureWrite(record, classifyRecordError({ code: "concurrent_update" }), 1).blocked).toBe(true);
    expect(decideBlockedWrite(record, classifyRecordError({ code: "not_found" }), 1).record.sync_status).toBe("blocked");
  });
});
