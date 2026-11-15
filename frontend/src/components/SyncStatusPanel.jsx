import React, { useState } from "react";
import { describeSyncProblem, summarizeSyncStates } from "../services/syncOutcome";
import { runSync, retryBlocked, discardBlocked, acknowledgeConflictNotice } from "../services/syncEngine";
import { useToast } from "../context/ToastContext";

const COUNTERS = [
  { key: "pending", label: "Pending", hint: "Saved on this device, waiting to be sent." },
  { key: "syncing", label: "Syncing", hint: "Being sent right now." },
  { key: "synced", label: "Synced", hint: "Confirmed by the server." },
  { key: "conflicted", label: "Conflicted", hint: "The server kept a newer change instead of yours." },
  { key: "blocked", label: "Blocked", hint: "Can't sync by itself — needs your decision." },
];

/**
 * Shows, for everything on this device, how many records are pending / syncing / synced / conflicted /
 * blocked — and for every record that is NOT simply waiting or done, WHY, with the recovery action.
 * Nothing ever sits silently "pending": a record is either on its way, or explained here.
 */
export default function SyncStatusPanel({ deliveries, syncingIds, onChanged }) {
  const { showToast } = useToast();
  const [busyId, setBusyId] = useState(null);

  const summary = summarizeSyncStates(deliveries, syncingIds);
  const problems = deliveries
    .map((delivery) => ({ delivery, problem: describeSyncProblem(delivery) }))
    .filter((entry) => entry.problem);

  async function act(delivery, actionId) {
    setBusyId(delivery.id);
    try {
      if (actionId === "retry") {
        await retryBlocked(delivery.id);
        showToast(`Trying ${delivery.order_id} again…`, "info");
        await onChanged();
        const result = await runSync();
        if (result.outcome === "success") showToast(`${delivery.order_id} synced.`, "success");
      } else if (actionId === "discard") {
        const confirmed = window.confirm(
          `Discard your unsent change to ${delivery.order_id}? It will be removed from this device. The delivery on the server is not affected.`
        );
        if (!confirmed) return;
        await discardBlocked(delivery.id);
        showToast(`Discarded your change to ${delivery.order_id}.`, "info");
      } else if (actionId === "acknowledge") {
        await acknowledgeConflictNotice(delivery.id);
      }
    } catch (error) {
      showToast(`Couldn't do that: ${error.message}`, "error");
    } finally {
      setBusyId(null);
      await onChanged();
    }
  }

  if (summary.total === 0) return null;

  return (
    <div className="card sync-panel" aria-label="Sync status">
      <div className="sync-panel-title">Sync status</div>
      <div className="sync-counters">
        {COUNTERS.map(({ key, label, hint }) => (
          <div key={key} className={`sync-counter ${key} ${summary[key] > 0 ? "active" : ""}`} title={hint}>
            <span className="sync-counter-number">{summary[key]}</span>
            <span className="sync-counter-label">{label}</span>
          </div>
        ))}
      </div>

      {problems.length > 0 && (
        <div className="sync-problems">
          {problems.map(({ delivery, problem }) => (
            <div key={delivery.id} className={`sync-problem ${problem.tone}`}>
              <div className="sync-problem-text">
                <strong>{delivery.order_id}</strong> — {problem.title}
                <div className="sync-problem-detail">{problem.detail}</div>
                {problem.hint && <div className="sync-problem-hint">{problem.hint}</div>}
              </div>
              {problem.actions.length > 0 && (
                <div className="sync-problem-actions">
                  {problem.actions.map((action) => (
                    <button
                      key={action.id}
                      className="btn"
                      style={{ fontSize: "11px" }}
                      title={action.description}
                      disabled={busyId === delivery.id}
                      onClick={() => act(delivery, action.id)}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
