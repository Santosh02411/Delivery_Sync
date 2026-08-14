import React, { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { fetchAuditLog, fetchOrganizationUsers } from "../services/api";

const PAGE_SIZE = 50;

/**
 * Admin-facing audit log: browses delivery status-change history across
 * every delivery in the organization, filterable by date range, who
 * made the change, and delivery/order ID. The underlying data (who
 * changed what, when) was already being recorded for the per-delivery
 * history timeline — this is the first place it's browsable broadly,
 * across the whole org at once.
 */
export default function AuditLogViewer() {
  const { token } = useAuth();
  const { showToast } = useToast();

  const [entries, setEntries] = useState([]);
  const [users, setUsers] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [changedByUserId, setChangedByUserId] = useState("");
  const [orderIdFilter, setOrderIdFilter] = useState("");

  useEffect(() => {
    fetchOrganizationUsers(token).then(setUsers).catch(() => {});
  }, [token]);

  useEffect(() => {
    load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFrom, dateTo, changedByUserId, orderIdFilter]);

  async function load(newOffset) {
    setIsLoading(true);
    try {
      const data = await fetchAuditLog(token, {
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        changedByUserId: changedByUserId || undefined,
        orderId: orderIdFilter || undefined,
        limit: PAGE_SIZE,
        offset: newOffset,
      });
      setEntries((prev) => (newOffset === 0 ? data : [...prev, ...data]));
      setHasMore(data.length === PAGE_SIZE);
      setOffset(newOffset);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setIsLoading(false);
    }
  }

  function handleLoadMore() {
    load(offset + PAGE_SIZE);
  }

  function clearFilters() {
    setDateFrom("");
    setDateTo("");
    setChangedByUserId("");
    setOrderIdFilter("");
  }

  const hasFilters = dateFrom || dateTo || changedByUserId || orderIdFilter;

  return (
    <div>
      <h2 className="page-title">Audit Log</h2>
      <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginBottom: "16px" }}>
        Every status change made to a delivery in your organization, in order — who made it and when.
      </p>

      <div className="card" style={{ marginBottom: "16px", display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "flex-end" }}>
        <div className="auth-field" style={{ marginBottom: 0 }}>
          <label>From</label>
          <input type="date" className="input" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div className="auth-field" style={{ marginBottom: 0 }}>
          <label>To</label>
          <input type="date" className="input" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>
        <div className="auth-field" style={{ marginBottom: 0 }}>
          <label>Changed by</label>
          <select className="input" value={changedByUserId} onChange={(e) => setChangedByUserId(e.target.value)}>
            <option value="">Anyone</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.display_name}</option>
            ))}
          </select>
        </div>
        <div className="auth-field" style={{ marginBottom: 0 }}>
          <label>Order ID contains</label>
          <input
            type="text"
            className="input"
            value={orderIdFilter}
            onChange={(e) => setOrderIdFilter(e.target.value)}
            placeholder="e.g. ORD-1023"
          />
        </div>
        {hasFilters && (
          <button className="btn" onClick={clearFilters}>Clear filters</button>
        )}
      </div>

      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Order</th>
              <th>Changed By</th>
              <th>From</th>
              <th>To</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td style={{ whiteSpace: "nowrap" }}>{new Date(e.changed_at).toLocaleString()}</td>
                <td className="mono">{e.delivery_order_id}</td>
                <td>{e.changed_by_display_name}</td>
                <td style={{ textTransform: "capitalize" }}>{e.old_status ? e.old_status.replace(/_/g, " ") : "—"}</td>
                <td style={{ textTransform: "capitalize" }}>{e.new_status.replace(/_/g, " ")}</td>
                <td style={{ color: "var(--text-secondary)" }}>{e.note || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!isLoading && entries.length === 0 && (
        <p style={{ color: "var(--text-secondary)", marginTop: "12px" }}>
          No audit entries match these filters yet.
        </p>
      )}

      {hasMore && (
        <button className="btn" style={{ marginTop: "12px" }} onClick={handleLoadMore} disabled={isLoading}>
          {isLoading ? "Loading..." : "Load more"}
        </button>
      )}
    </div>
  );
}
