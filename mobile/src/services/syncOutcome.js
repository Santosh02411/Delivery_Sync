/**
 * Sync states, error classification and outcomes — the rules that make sure EVERY locally-saved
 * record always has a clear, visible explanation of where it stands.
 *
 * THE PROBLEM THIS SOLVES
 * -----------------------
 * Before, the server could refuse a record (e.g. "this delivery isn't assigned to you") and the
 * engine still reported `success: true`. The refused record never became "synced", so it stayed
 * "pending" and was re-sent on every 15-second cycle, forever, with nothing telling the agent why.
 * One malformed record could also make the server reject the WHOLE batch (HTTP 422), blocking every
 * other record behind it. And in the service worker, failed background operations were swallowed, so
 * the browser was never asked to retry.
 *
 * THE MODEL
 * ---------
 * Every record is in exactly one visible state (`sync_status` on the stored record):
 *
 *   pending     saved on this device, not yet confirmed by the server. May carry `sync_error`
 *               (kind "retryable") explaining why the last attempt didn't go through and that it
 *               will be retried automatically — with an attempt counter, so it can't loop forever.
 *   syncing     being uploaded right now. NOT stored (a crash mid-upload would leave it stuck);
 *               derived from the engine's in-memory set of in-flight ids.
 *   synced      the server has it and the local copy matches.
 *   conflicted  the server refused/overrode the change (someone else's newer change won). The server's
 *               copy is stored, `sync_conflict` explains what happened, and it stays visible until the
 *               agent acknowledges it. Never re-sent.
 *   blocked     cannot sync automatically (a permanent error, or retries exhausted). The record is kept
 *               exactly as the agent left it — it is the dead-letter queue — together with
 *               `sync_error` (reason) and recovery actions: retry, or discard. NEVER re-sent on its
 *               own, so it cannot loop.
 *
 * Errors are classified as RETRYABLE (will probably work later: network, server busy, a concurrent
 * update) or PERMANENT (waiting won't help: not assigned to you, no longer exists, invalid data).
 *
 * Whole-run results are one of three explicit outcomes:
 *   success          everything that was pending is now synced (or nothing was pending)
 *   partial_success  some records were settled, but others are blocked / retrying / conflicted / unsent
 *   failed           nothing could be settled (network down, session expired, everything rejected)
 *
 * These functions are PURE (no storage, no network). This is the mobile copy of
 * frontend/src/services/syncOutcome.js (the two apps are separate packages and can't import each
 * other) — keep them identical. mobile/src/services/__tests__/syncOutcome.test.js checks that this copy
 * agrees with the web one.
 */

import { revisionOf } from "./syncRevision";

export const SYNC_STATE = Object.freeze({
  PENDING: "pending",
  SYNCING: "syncing",
  SYNCED: "synced",
  CONFLICTED: "conflicted",
  BLOCKED: "blocked",
});

export const OUTCOME = Object.freeze({
  SUCCESS: "success",
  PARTIAL_SUCCESS: "partial_success",
  FAILED: "failed",
});

/** A record the server keeps answering with a retryable error is blocked after this many attempts. */
export const MAX_RECORD_ATTEMPTS = 5;
/** The server accepts up to 200 records per request; stay well under it. */
export const MAX_BATCH_SIZE = 100;

export const RECOVERY_ACTIONS = Object.freeze({
  retry: { id: "retry", label: "Try again", description: "Send this update to the server again." },
  discard: { id: "discard", label: "Discard my change", description: "Remove this update from this device (the server's version is not affected)." },
  acknowledge: { id: "acknowledge", label: "Got it", description: "Dismiss this notice." },
});

/**
 * The idempotency key for ONE local edit: generated when the agent saves the edit, kept unchanged through every
 * retry (including "try again" on a blocked record), and sent to /sync as `event_id`. The server applies a given
 * event at most once, so a resend after a lost response can never double-apply a change.
 */
export function newEventId() {
  const c = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const hex = (n) => Array.from({ length: n }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  return `${Date.now().toString(16)}-${hex(8)}-${hex(8)}-${hex(8)}`; // fallback (older WebViews / React Native)
}

/** Fields that exist only on this device; never sent to the server, cleared on every fresh local edit. */
const LOCAL_SYNC_FIELDS = ["sync_error", "sync_attempts", "sync_conflict"];

// --------------------------------------------------------------------------------------
// Classifying what the server said about ONE record (an entry in `errors`)
// --------------------------------------------------------------------------------------

const RECORD_ERROR_RULES = {
  not_assigned: {
    kind: "permanent", title: "No longer assigned to you",
    hint: "A dispatcher may have reassigned or removed this delivery, so your change can't be applied.",
    actions: ["discard"],
  },
  identity_mismatch: {
    kind: "permanent", title: "Saved under a different account",
    hint: "This change was recorded while signed in as someone else, so it can't be sent from this account.",
    actions: ["discard"],
  },
  not_found: {
    kind: "permanent", title: "Delivery no longer exists",
    hint: "The delivery was removed on the server.",
    actions: ["discard"],
  },
  invalid_agent: {
    kind: "permanent", title: "Agent not valid",
    hint: "The agent on this record doesn't exist in your organization.",
    actions: ["discard"],
  },
  invalid_record: {
    kind: "permanent", title: "The server rejected this update",
    hint: "Something in this update isn't valid, so retrying won't help.",
    actions: ["discard"],
  },
  base_version_required: {
    kind: "permanent", title: "Out of date",
    hint: "This update was saved before the delivery's version was known. Discard it and make the change again.",
    actions: ["discard"],
  },
  rejected_by_server: {
    kind: "permanent", title: "The server refused this update",
    hint: "The server turned this record down as invalid, so retrying won't help.",
    actions: ["discard"],
  },
  retry_exhausted: {
    kind: "permanent", title: "Couldn't sync after several tries",
    hint: "It failed repeatedly. Try again when you have a better connection, or ask your dispatcher.",
    actions: ["retry", "discard"],
  },
  concurrent_update: {
    kind: "retryable", title: "Changed by someone else at the same moment",
    hint: "It will be retried automatically.",
    actions: [],
  },
  server_error: {
    kind: "retryable", title: "The server had a problem with this update",
    hint: "It will be retried automatically.",
    actions: [],
  },
  // Not a failure: the delivery can't be marked delivered until its proof of delivery has reached the server
  // (it has its own upload queue). It waits — without using up retry attempts — and syncs by itself afterwards.
  pod_required: {
    kind: "retryable", waiting: true, title: "Waiting for proof of delivery",
    hint: "It will sync automatically once the proof of delivery has been uploaded.",
    actions: [],
  },
};

const UNKNOWN_RULE = {
  kind: "retryable", title: "Couldn't sync this update",
  hint: "It will be retried automatically a few times.",
  actions: [],
};

/**
 * Turns one server error ({ id, error|message, code }) — or a locally-made one ({ code, message }) —
 * into the shape stored on the record as `sync_error`.
 * Unknown codes are treated as retryable, but are bounded by MAX_RECORD_ATTEMPTS, so even a kind of
 * failure nobody anticipated ends up blocked-with-a-reason instead of looping forever.
 */
export function classifyRecordError(serverError) {
  const code = (serverError && serverError.code) || "unknown_error";
  const rule = RECORD_ERROR_RULES[code] || UNKNOWN_RULE;
  const serverText = serverError && (serverError.error || serverError.message);
  return {
    code,
    kind: rule.kind,
    waiting: Boolean(rule.waiting), // true: waiting for something else to arrive, so it never uses up retry attempts
    title: rule.title,
    message: typeof serverText === "string" && serverText ? serverText : rule.title,
    hint: rule.hint,
    actions: [...rule.actions],
  };
}

/** FastAPI's `detail` is a string, or a list of { loc, msg }. Returns one readable sentence. */
export function formatServerDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (!item || !item.msg) return null;
        const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body" && p !== "records" && !Number.isInteger(p)).join(".") : "";
        return loc ? `${loc}: ${item.msg}` : item.msg;
      })
      .filter(Boolean);
    if (parts.length) return parts.slice(0, 3).join("; ");
  }
  return null;
}

/**
 * When the server refuses a whole batch with a validation error (HTTP 422), `detail` points at the
 * offending records by position (loc: ["body","records",<index>,...]). Returns [{ index, message }]
 * for the valid indexes, so those records can be blocked directly instead of probing for them.
 */
export function extractRecordFaults(detail, batchLength) {
  if (!Array.isArray(detail)) return [];
  const byIndex = new Map();
  for (const item of detail) {
    const loc = item && item.loc;
    if (!Array.isArray(loc) || loc[0] !== "body" || loc[1] !== "records" || !Number.isInteger(loc[2])) continue;
    if (loc[2] < 0 || loc[2] >= batchLength) continue;
    const field = loc.slice(3).join(".");
    const text = item.msg ? (field ? `${field}: ${item.msg}` : item.msg) : "invalid";
    byIndex.set(loc[2], byIndex.has(loc[2]) ? `${byIndex.get(loc[2])}; ${text}` : text);
  }
  return [...byIndex.entries()].map(([index, message]) => ({ index, message }));
}

// --------------------------------------------------------------------------------------
// Classifying a failed REQUEST (the whole call, not one record)
// --------------------------------------------------------------------------------------

/**
 * scope "auth"      401 / 403: the session is the problem; stop, nothing is lost, nobody is blamed.
 * scope "rejected"  400 / 413 / 422: the server refused the PAYLOAD itself. Deterministic — some record in
 *                   the batch is at fault, so the batch is split to find (and block) it.
 * scope "transport" no response, or a transient status (408/425/429/502/503/504): try again later.
 * scope "server"    any other status (500, 404 from a misconfigured URL, ...): treated as an outage —
 *                   retried with backoff and NEVER blamed on a record (a wrong URL must not block data).
 */
export function classifyRequestError(error) {
  const status = error && error.status;
  if (status === 401 || status === 403) return { scope: "auth", status };
  if (!Number.isInteger(status) || [408, 425, 429, 502, 503, 504].includes(status)) return { scope: "transport", status };
  if ([400, 413, 422].includes(status)) return { scope: "rejected", status };
  return { scope: "server", status };
}

// --------------------------------------------------------------------------------------
// Whole-run outcome
// --------------------------------------------------------------------------------------

/**
 * success          nothing needs attention
 * partial_success  at least one record got a final answer, but something still needs attention
 * failed           nothing got a final answer and something needs attention
 * A server-resolved conflict counts as "got a final answer" (the server's copy replaced ours) but also
 * "needs attention" (the agent's change was not applied) — so a conflict is never reported as plain success.
 */
export function decideOutcome({ syncedCount = 0, conflictCount = 0, blockedCount = 0, retryingCount = 0, requestFailed = false } = {}) {
  const needsAttention = blockedCount + retryingCount + conflictCount > 0 || requestFailed;
  if (!needsAttention) return OUTCOME.SUCCESS;
  return syncedCount + conflictCount > 0 ? OUTCOME.PARTIAL_SUCCESS : OUTCOME.FAILED;
}

/**
 * The honest one-line message for a finished run (shown as a toast). Never says "synced" about records
 * the server refused, and says what happens next when something is only waiting to be retried.
 * Returns { tone: "success" | "info" | "error", message }.
 */
export function summarizeRunResult(result) {
  if (!result || result.skipped) return { tone: "info", message: "A sync is already running — it will finish in a moment." };
  const synced = result.syncedCount || 0;
  const attention = (result.blockedCount || 0) + (result.retryingCount || 0) + (result.conflictCount || 0);
  const plural = (n, one, many) => (n === 1 ? one : many);

  if (result.outcome === OUTCOME.SUCCESS) {
    return synced > 0
      ? { tone: "success", message: `Synced ${synced} ${plural(synced, "record", "records")}.` }
      : { tone: "info", message: "Already up to date." };
  }
  if (result.authError) {
    return { tone: "error", message: "Your session expired — sign in again to sync. Your changes are saved on this device." };
  }
  if (result.requestFailed) {
    const done = synced > 0 ? `Synced ${synced}, then the connection failed. ` : "Couldn't reach the server. ";
    return { tone: "error", message: `${done}Your changes are saved on this device and will be retried automatically.` };
  }
  const needs = `${attention} ${plural(attention, "record needs", "records need")} attention — see Sync status below.`;
  return { tone: "error", message: result.outcome === OUTCOME.PARTIAL_SUCCESS ? `Synced ${synced}. ${needs}` : `Nothing could be synced. ${needs}` };
}

/** Wording for the service worker's "background-sync-complete" message (it can run with every tab closed). */
export function summarizeBackgroundSync(detail) {
  const synced = (detail && detail.syncedCount) || 0;
  const blocked = (detail && detail.blockedCount) || 0;
  const retry = (detail && detail.retryCount) || 0;
  const parts = [];
  if (synced > 0) parts.push(`Synced ${synced} queued item${synced === 1 ? "" : "s"}`);
  if (blocked > 0) parts.push(`${blocked} ${blocked === 1 ? "was" : "were"} refused by the server and can't sync`);
  if (retry > 0) parts.push(`${retry} will be retried automatically`);
  if (detail && detail.authExpired) parts.push("sign in again to sync the rest");
  if (parts.length === 0) return null;
  const tone = blocked > 0 || (detail && detail.authExpired) ? "error" : "info";
  const sentence = `${parts.join("; ")}.`;
  return { tone, message: sentence.charAt(0).toUpperCase() + sentence.slice(1) };
}

// --------------------------------------------------------------------------------------
// Records: payload, states, summary
// --------------------------------------------------------------------------------------

export function withoutLocalSyncFields(record) {
  const clean = { ...record };
  for (const field of LOCAL_SYNC_FIELDS) delete clean[field];
  return clean;
}

/** What is actually sent to /sync for a pending record. */
export function toSyncPayload(record) {
  return {
    ...withoutLocalSyncFields(record),
    local_revision: revisionOf(record),
    base_version: Number.isInteger(record.version) ? record.version : 0, // 0 = "unknown": treated as the oldest possible, so it can never override newer changes
  };
}

export function chunk(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/** The state to SHOW for a record: its stored state, or "syncing" while it is in flight. */
export function effectiveSyncState(record, syncingIds) {
  const stored = record && record.sync_status ? record.sync_status : SYNC_STATE.SYNCED;
  if (stored === SYNC_STATE.PENDING && syncingIds && syncingIds.has(record.id)) return SYNC_STATE.SYNCING;
  return stored;
}

/** Counts for the UI: pending, syncing, synced, conflicted, blocked (+ total and how many need attention). */
export function summarizeSyncStates(records, syncingIds = new Set()) {
  const counts = { pending: 0, syncing: 0, synced: 0, conflicted: 0, blocked: 0 };
  for (const record of records || []) {
    const state = effectiveSyncState(record, syncingIds);
    counts[state in counts ? state : "synced"] += 1;
  }
  const total = counts.pending + counts.syncing + counts.synced + counts.conflicted + counts.blocked;
  const retrying = (records || []).filter((r) => r.sync_status === SYNC_STATE.PENDING && r.sync_error && r.sync_error.kind === "retryable").length;
  return { ...counts, total, retrying, needsAttention: counts.blocked + counts.conflicted + retrying };
}

/**
 * Everything the UI needs to explain a record that isn't simply "pending" or "synced":
 * { state, title, detail, hint, actions: [{id,label,description}], tone } — or null for a healthy record.
 */
export function describeSyncProblem(record) {
  if (!record) return null;
  if (record.sync_status === SYNC_STATE.BLOCKED) {
    const err = record.sync_error || classifyRecordError({ code: "unknown_error" });
    return {
      state: SYNC_STATE.BLOCKED, tone: "danger", title: err.title, detail: err.message, hint: err.hint,
      actions: (err.actions || []).map((id) => RECOVERY_ACTIONS[id]).filter(Boolean),
    };
  }
  if (record.sync_status === SYNC_STATE.CONFLICTED) {
    const c = record.sync_conflict || {};
    return {
      state: SYNC_STATE.CONFLICTED, tone: "warning", title: "Your change wasn't applied",
      detail: c.message || "A newer change on the server took priority.",
      hint: "The delivery now shows the server's version.",
      actions: [RECOVERY_ACTIONS.acknowledge],
    };
  }
  if (record.sync_status === SYNC_STATE.PENDING && record.sync_error) {
    const attempts = Number.isInteger(record.sync_attempts) ? record.sync_attempts : 0;
    if (record.sync_error.waiting) {
      return { state: SYNC_STATE.PENDING, tone: "warning", title: record.sync_error.title, detail: record.sync_error.message, hint: record.sync_error.hint, actions: [] };
    }
    return {
      state: SYNC_STATE.PENDING, tone: "warning", title: "Will retry automatically",
      detail: `${record.sync_error.message} (attempt ${attempts} of ${MAX_RECORD_ATTEMPTS})`,
      hint: record.sync_error.hint, actions: [],
    };
  }
  return null;
}

/** One-line wording for the badge/tooltip of any state. */
export function syncStateLabel(state) {
  switch (state) {
    case SYNC_STATE.SYNCED: return "Synced";
    case SYNC_STATE.SYNCING: return "Syncing…";
    case SYNC_STATE.CONFLICTED: return "Conflict";
    case SYNC_STATE.BLOCKED: return "Blocked";
    default: return "Saved locally";
  }
}

/**
 * Turns one backend conflict record (see services/conflict_resolver.py) into a plain-English
 * sentence for the UI: a discarded offline change should never vanish with no trace.
 */
export function describeConflict(conflict) {
  // The server explains refusals itself (e.g. "This delivery was cancelled after you last synced, so
  // your change to 'delivered' was not applied."): which change wins is decided by version checks and
  // the transition policy, not by who is newer.
  if (conflict.message) return `Order ${conflict.order_id}: ${conflict.message}`;
  const who = conflict.kept_by ? `${conflict.kept_by} already updated it` : "it was already updated";
  return `Order ${conflict.order_id}: your change to "${conflict.your_status}" was overridden — ${who} to "${conflict.kept_status}" more recently, so that's what was kept.`;
}

// --------------------------------------------------------------------------------------
// State transitions. Each takes the record as stored RIGHT NOW (read in the same transaction that will
// write it) and returns { write: false, reason } or { write: true, record, ... } — same shape and same
// lost-update guard as decideSyncedWrite in syncRevision.js: an answer is only applied if the record is
// still at the revision that was sent, so a newer edit is never overwritten.
// --------------------------------------------------------------------------------------

function guardAgainstStaleAnswer(current, submittedRevision) {
  if (!current) return { write: false, reason: "deleted_locally" };
  if (current.sync_status !== SYNC_STATE.PENDING) return { write: false, reason: "already_settled" };
  if (revisionOf(current) !== submittedRevision) return { write: false, reason: "changed_during_sync" };
  return null;
}

function nowIso() {
  return new Date().toISOString();
}

/** The server refused this revision permanently: quarantine it (dead-letter) with its reason. */
export function decideBlockedWrite(current, info, submittedRevision) {
  const stale = guardAgainstStaleAnswer(current, submittedRevision);
  if (stale) return stale;
  return {
    write: true, blocked: true,
    record: { ...current, sync_status: SYNC_STATE.BLOCKED, sync_error: { ...info, at: nowIso() } },
  };
}

/** A retryable failure for this record: count it, and block it once it has failed MAX_RECORD_ATTEMPTS times. */
export function decideRetryableFailureWrite(current, info, submittedRevision) {
  const stale = guardAgainstStaleAnswer(current, submittedRevision);
  if (stale) return stale;
  if (info.waiting) {
    // Waiting for something else to arrive (e.g. proof of delivery): show why, but don't count it as a failed
    // attempt — it must never be blocked just because the other upload is slow.
    return { write: true, blocked: false, record: { ...current, sync_error: { ...info, at: nowIso() } } };
  }
  const attempts = (Number.isInteger(current.sync_attempts) ? current.sync_attempts : 0) + 1;
  if (attempts >= MAX_RECORD_ATTEMPTS) {
    const exhausted = classifyRecordError({ code: "retry_exhausted", message: `${info.message} (failed ${attempts} times)` });
    return {
      write: true, blocked: true,
      record: { ...current, sync_status: SYNC_STATE.BLOCKED, sync_attempts: attempts, sync_error: { ...exhausted, at: nowIso() } },
    };
  }
  return {
    write: true, blocked: false,
    record: { ...current, sync_attempts: attempts, sync_error: { ...info, at: nowIso() } },
  };
}

/** The server answered, but did NOT apply this change (a newer change won): keep ITS copy and say so. */
export function decideConflictWrite(current, resolved, conflict, submittedRevision) {
  const stale = guardAgainstStaleAnswer(current, submittedRevision);
  if (stale) return stale;
  return {
    write: true,
    record: {
      ...withoutLocalSyncFields(resolved),
      sync_status: SYNC_STATE.CONFLICTED,
      local_revision: revisionOf(current),
      sync_conflict: {
        code: conflict.code || null,
        message: describeConflict(conflict),
        your_status: conflict.your_status || null,
        kept_status: conflict.kept_status || null,
        at: nowIso(),
      },
    },
  };
}

/** Recovery: put a blocked record back in the queue (fresh attempt counter, error cleared). */
export function decideRetryBlocked(current) {
  if (!current) return { write: false, reason: "deleted_locally" };
  if (current.sync_status !== SYNC_STATE.BLOCKED) return { write: false, reason: "not_blocked" };
  return { write: true, record: { ...withoutLocalSyncFields(current), sync_status: SYNC_STATE.PENDING } };
}

/** Recovery: the agent has seen the conflict; the record (already the server's copy) becomes plain "synced". */
export function decideAcknowledgeConflict(current) {
  if (!current) return { write: false, reason: "deleted_locally" };
  if (current.sync_status !== SYNC_STATE.CONFLICTED) return { write: false, reason: "not_conflicted" };
  return { write: true, record: { ...withoutLocalSyncFields(current), sync_status: SYNC_STATE.SYNCED } };
}
