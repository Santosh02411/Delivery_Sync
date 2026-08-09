import React, { useEffect, useState } from "react";
import { v4 as uuidv4 } from "uuid";
import {
  fetchAllDeliveriesFromServer,
  fetchAgentsList,
  createDeliveryOnServer,
  exportDeliveriesCSV,
  fetchUnassignedDeliveries,
  assignAgentToDelivery,
} from "../services/api";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import DeliveryDetailModal from "./DeliveryDetailModal";
import StatusBadge from "./StatusBadge";
import Pagination from "./Pagination";
import BulkImportPanel from "./BulkImportPanel";

const STATUS_FILTER_OPTIONS = [
  { value: "all", label: "All Statuses" },
  { value: "pending", label: "Pending (Unassigned)" },
  { value: "picked_up", label: "Picked Up" },
  { value: "out_for_delivery", label: "Out for Delivery" },
  { value: "delivered", label: "Delivered" },
  { value: "failed_attempt", label: "Failed Attempt" },
  { value: "cancelled", label: "Cancelled" },
];

const SORT_OPTIONS = [
  { value: "updated_desc", label: "Most Recently Updated" },
  { value: "updated_asc", label: "Oldest Updated First" },
  { value: "order_id_asc", label: "Order ID (A-Z)" },
  { value: "status", label: "Status" },
];

const PAGE_SIZE = 8;

/**
 * Dashboard for dispatchers/managers — summary stats, assign new
 * deliveries, and browse all deliveries with status/agent/date filters,
 * sorting, search, pagination, and a click-through detail modal.
 */
export default function DispatcherTable() {
  const { token } = useAuth();
  const { showToast } = useToast();
  const [deliveries, setDeliveries] = useState([]);
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState(null);

  const [statusFilter, setStatusFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortOption, setSortOption] = useState("updated_desc");
  const [currentPage, setCurrentPage] = useState(1);

  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [newOrderId, setNewOrderId] = useState("");
  const [newNotes, setNewNotes] = useState("");
  const [newZone, setNewZone] = useState("");
  const [newExpectedBy, setNewExpectedBy] = useState("");
  const [newCustomerEmail, setNewCustomerEmail] = useState("");
  const [newCustomerPhone, setNewCustomerPhone] = useState("");
  const [showCoordFields, setShowCoordFields] = useState(false);
  const [newLatitude, setNewLatitude] = useState("");
  const [newLongitude, setNewLongitude] = useState("");
  const [isAssigning, setIsAssigning] = useState(false);

  const [selectedDelivery, setSelectedDelivery] = useState(null);

  useEffect(() => {
    loadDeliveries();
    loadAgents();
  }, []);

  useEffect(() => {
    setCurrentPage(1);
  }, [statusFilter, agentFilter, dateFrom, dateTo, searchQuery]);

  async function loadDeliveries() {
    try {
      const records = await fetchAllDeliveriesFromServer(token);
      setDeliveries(records);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadAgents() {
    try {
      const agentList = await fetchAgentsList(token);
      setAgents(agentList);
      if (agentList.length > 0) setSelectedAgentId(agentList[0].id);
    } catch (err) {
      console.warn("Could not load agents list:", err.message);
    }
  }

  async function handleExportCSV() {
    try {
      await exportDeliveriesCSV(token, dateFrom, dateTo);
      showToast("CSV export downloaded.", "success");
    } catch (err) {
      showToast(`Export failed: ${err.message}`, "error");
    }
  }

  async function handleAssignDelivery(e) {
    e.preventDefault();

    if (!selectedAgentId || !newOrderId.trim()) {
      showToast("Pick an agent and enter an order ID.", "error");
      return;
    }

    // If either coordinate is filled, both must be filled and must be valid numbers —
    // a half-entered coordinate pair would silently break route optimization later
    const latTrimmed = newLatitude.trim();
    const lonTrimmed = newLongitude.trim();
    if ((latTrimmed || lonTrimmed) && (!latTrimmed || !lonTrimmed)) {
      showToast("Enter both latitude and longitude, or leave both blank.", "error");
      return;
    }
    if (latTrimmed && (isNaN(parseFloat(latTrimmed)) || isNaN(parseFloat(lonTrimmed)))) {
      showToast("Latitude and longitude must be numbers.", "error");
      return;
    }

    setIsAssigning(true);
    const now = new Date().toISOString();
    try {
      await createDeliveryOnServer(token, {
        id: uuidv4(),
        agent_id: selectedAgentId,
        order_id: newOrderId.trim(),
        status: "picked_up",
        notes: newNotes.trim(),
        location_note: "",
        zone: newZone.trim() || null,
        customer_email: newCustomerEmail.trim() || null,
        customer_phone: newCustomerPhone.trim() || null,
        latitude: latTrimmed || null,
        longitude: lonTrimmed || null,
        expected_by: newExpectedBy ? new Date(newExpectedBy).toISOString() : null,
        created_at: now,
        updated_at: now,
      });
      showToast(`Assigned ${newOrderId} successfully.`, "success");
      setNewOrderId("");
      setNewNotes("");
      setNewZone("");
      setNewExpectedBy("");
      setNewCustomerEmail("");
      setNewCustomerPhone("");
      setNewLatitude("");
      setNewLongitude("");
      await loadDeliveries();
    } catch (err) {
      showToast(`Failed to assign: ${err.message}`, "error");
    } finally {
      setIsAssigning(false);
    }
  }

  const agentNameById = new Map(agents.map((a) => [a.id, a.display_name]));

  const today = new Date().toDateString();
  const statCounts = { picked_up: 0, out_for_delivery: 0, delivered: 0, failed_attempt: 0 };
  let deliveredToday = 0;
  for (const d of deliveries) {
    if (statCounts[d.status] !== undefined) statCounts[d.status] += 1;
    if (d.status === "delivered" && new Date(d.updated_at).toDateString() === today) {
      deliveredToday += 1;
    }
  }

  let filteredDeliveries = deliveries.filter((d) => {
    const matchesStatus = statusFilter === "all" || d.status === statusFilter;
    const matchesAgent = agentFilter === "all" || d.agent_id === agentFilter;

    const updatedDate = new Date(d.updated_at);
    const matchesFrom = !dateFrom || updatedDate >= new Date(dateFrom);
    const matchesTo = !dateTo || updatedDate <= new Date(dateTo + "T23:59:59");

    const agentName = agentNameById.get(d.agent_id) || d.agent_id;
    const query = searchQuery.toLowerCase();
    const matchesSearch =
      query === "" ||
      d.order_id.toLowerCase().includes(query) ||
      agentName.toLowerCase().includes(query);

    return matchesStatus && matchesAgent && matchesFrom && matchesTo && matchesSearch;
  });

  filteredDeliveries = [...filteredDeliveries].sort((a, b) => {
    switch (sortOption) {
      case "updated_asc":
        return new Date(a.updated_at) - new Date(b.updated_at);
      case "order_id_asc":
        return a.order_id.localeCompare(b.order_id);
      case "status":
        return a.status.localeCompare(b.status);
      case "updated_desc":
      default:
        return new Date(b.updated_at) - new Date(a.updated_at);
    }
  });

  const totalPages = Math.max(1, Math.ceil(filteredDeliveries.length / PAGE_SIZE));
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const visibleDeliveries = filteredDeliveries.slice(pageStart, pageStart + PAGE_SIZE);

  function clearFilters() {
    setStatusFilter("all");
    setAgentFilter("all");
    setDateFrom("");
    setDateTo("");
    setSearchQuery("");
  }

  return (
    <div>
      <h2 className="page-title">Dispatcher Dashboard</h2>

      <UnassignedOrdersPanel token={token} agents={agents} onAssigned={loadDeliveries} />

      <div style={{ display: "flex", gap: "12px", marginBottom: "24px", flexWrap: "wrap" }}>
        <div className="stat-card">
          <div className="stat-card-value">{statCounts.picked_up}</div>
          <div className="stat-card-label">Picked Up</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{statCounts.out_for_delivery}</div>
          <div className="stat-card-label">Out for Delivery</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{statCounts.delivered}</div>
          <div className="stat-card-label">Delivered</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{statCounts.failed_attempt}</div>
          <div className="stat-card-label">Failed Attempts</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{deliveredToday}</div>
          <div className="stat-card-label">Delivered Today</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: "24px" }}>
        <h3 style={{ marginBottom: "12px" }}>Assign a New Delivery</h3>
        {agents.length === 0 && (
          <p style={{ color: "var(--text-secondary)" }}>
            No agents registered yet. Have an agent sign up first.
          </p>
        )}
        {agents.length > 0 && (
          <form onSubmit={handleAssignDelivery} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "flex-end" }}>
              <div>
                <label className="field-label">Agent</label>
                <select className="input" value={selectedAgentId} onChange={(e) => setSelectedAgentId(e.target.value)}>
                  {agents.map((agent) => (
                    <option key={agent.id} value={agent.id}>{agent.display_name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="field-label">Order ID</label>
                <input className="input" type="text" value={newOrderId} onChange={(e) => setNewOrderId(e.target.value)} placeholder="order-123" />
              </div>
              <div>
                <label className="field-label">Zone (optional)</label>
                <input className="input" type="text" value={newZone} onChange={(e) => setNewZone(e.target.value)} placeholder="e.g. North" />
              </div>
              <div>
                <label className="field-label">Expected By (optional)</label>
                <input className="input" type="datetime-local" value={newExpectedBy} onChange={(e) => setNewExpectedBy(e.target.value)} />
              </div>
              <div style={{ flexGrow: 1, minWidth: "150px" }}>
                <label className="field-label">Notes (optional)</label>
                <input className="input" type="text" value={newNotes} onChange={(e) => setNewNotes(e.target.value)} placeholder="Fragile, deliver before 5pm..." style={{ width: "100%" }} />
              </div>
              <div>
                <label className="field-label">Customer Email (optional)</label>
                <input className="input" type="email" value={newCustomerEmail} onChange={(e) => setNewCustomerEmail(e.target.value)} placeholder="customer@example.com" />
              </div>
              <div>
                <label className="field-label">Customer Phone (optional)</label>
                <input className="input" type="text" value={newCustomerPhone} onChange={(e) => setNewCustomerPhone(e.target.value)} placeholder="+15551234567" />
              </div>
            </div>

            <div>
              <button
                type="button"
                onClick={() => setShowCoordFields(!showCoordFields)}
                style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: "12.5px", padding: 0 }}
              >
                {showCoordFields ? "− Hide" : "+ Add"} coordinates (enables route optimization for the agent)
              </button>
            </div>

            {showCoordFields && (
              <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
                <div>
                  <label className="field-label">Latitude</label>
                  <input className="input" type="text" value={newLatitude} onChange={(e) => setNewLatitude(e.target.value)} placeholder="12.9716" />
                </div>
                <div>
                  <label className="field-label">Longitude</label>
                  <input className="input" type="text" value={newLongitude} onChange={(e) => setNewLongitude(e.target.value)} placeholder="77.5946" />
                </div>
              </div>
            )}

            <div>
              <button type="submit" className="btn btn-primary" disabled={isAssigning}>
                {isAssigning ? "Assigning..." : "Assign"}
              </button>
            </div>
          </form>
        )}
      </div>

      <BulkImportPanel onImportComplete={loadDeliveries} />

      <div className="card" style={{ marginBottom: "16px", display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "flex-end" }}>
        <button className="btn" onClick={loadDeliveries}>Refresh</button>

        <div>
          <label className="field-label">Status</label>
          <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            {STATUS_FILTER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div>
          <label className="field-label">Agent</label>
          <select className="input" value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
            <option value="all">All Agents</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.display_name}</option>)}
          </select>
        </div>

        <div>
          <label className="field-label">From</label>
          <input className="input" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>

        <div>
          <label className="field-label">To</label>
          <input className="input" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
        </div>

        <div>
          <label className="field-label">Sort by</label>
          <select className="input" value={sortOption} onChange={(e) => setSortOption(e.target.value)}>
            {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <input
          className="input"
          type="text"
          placeholder="Search by order ID or agent name..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ flexGrow: 1, minWidth: "200px" }}
        />

        <button className="btn" onClick={clearFilters}>Clear Filters</button>

        <button className="btn btn-primary" onClick={handleExportCSV}>
          Export CSV{(dateFrom || dateTo) ? " (filtered range)" : ""}
        </button>

        <span style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
          {filteredDeliveries.length} of {deliveries.length}
        </span>
      </div>

      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Order ID</th>
              <th>Customer</th>
              <th>Agent</th>
              <th>Zone</th>
              <th>Status</th>
              <th>Expected By</th>
              <th>Last Updated</th>
            </tr>
          </thead>
          <tbody>
            {visibleDeliveries.map((d) => {
              const isOverdue =
                d.expected_by &&
                d.status !== "delivered" &&
                new Date(d.expected_by) < new Date();
              return (
                <tr key={d.id} onClick={() => setSelectedDelivery(d)}>
                  <td className="mono">{d.order_id}</td>
                  <td style={{ fontSize: "12.5px" }}>
                    {d.customer_email || d.customer_phone
                      ? [d.customer_email, d.customer_phone].filter(Boolean).join(" · ")
                      : "—"}
                  </td>
                  <td>{agentNameById.get(d.agent_id) || d.agent_id}</td>
                  <td>{d.zone || "—"}</td>
                  <td><StatusBadge status={d.status} /></td>
                  <td style={{ color: isOverdue ? "var(--danger)" : undefined, fontWeight: isOverdue ? 600 : undefined }}>
                    {d.expected_by ? new Date(d.expected_by).toLocaleString() : "—"}
                    {isOverdue && " (Overdue)"}
                  </td>
                  <td>{new Date(d.updated_at).toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {visibleDeliveries.length === 0 && (
        <p style={{ color: "var(--text-secondary)", marginTop: "12px" }}>
          No deliveries match this filter/search.
        </p>
      )}

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        onPageChange={setCurrentPage}
        totalItems={filteredDeliveries.length}
        pageSize={PAGE_SIZE}
      />

      {selectedDelivery && (
        <DeliveryDetailModal
          delivery={selectedDelivery}
          agentName={agentNameById.get(selectedDelivery.agent_id)}
          onClose={() => setSelectedDelivery(null)}
        />
      )}
    </div>
  );
}

function UnassignedOrdersPanel({ token, agents, onAssigned }) {
  const [orders, setOrders] = useState([]);
  const [assigningId, setAssigningId] = useState(null);
  const [selectedAgentByOrder, setSelectedAgentByOrder] = useState({});
  const [error, setError] = useState(null);

  useEffect(() => {
    load();
    const intervalId = setInterval(load, 15000); // catches new checkout orders without a manual refresh
    return () => clearInterval(intervalId);
  }, []);

  async function load() {
    try {
      const data = await fetchUnassignedDeliveries(token);
      setOrders(data);
    } catch (err) {
      console.warn("Could not load unassigned orders:", err.message);
    }
  }

  async function handleAssign(orderId) {
    const agentId = selectedAgentByOrder[orderId];
    if (!agentId) {
      setError("Pick an agent first.");
      return;
    }
    setAssigningId(orderId);
    setError(null);
    try {
      await assignAgentToDelivery(token, orderId, agentId);
      await load();
      await onAssigned();
    } catch (err) {
      setError(err.message);
    } finally {
      setAssigningId(null);
    }
  }

  if (orders.length === 0) return null;

  return (
    <div className="card" style={{ marginBottom: "20px", borderColor: "var(--accent)" }}>
      <strong style={{ fontSize: "13.5px" }}>
        🛒 Unassigned Orders ({orders.length})
      </strong>
      <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "4px", marginBottom: "12px" }}>
        Placed and paid for via the storefront — assign an agent to start fulfillment.
      </p>
      {error && <p style={{ color: "var(--danger)", fontSize: "12px" }}>{error}</p>}
      <div style={{ display: "grid", gap: "10px" }}>
        {orders.map((order) => (
          <div
            key={order.id}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "8px 0",
              borderBottom: "1px solid var(--border-color)",
              flexWrap: "wrap",
              gap: "8px",
            }}
          >
            <div>
              <div style={{ fontSize: "13px", fontWeight: 600 }}>{order.order_id}</div>
              <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{order.notes}</div>
            </div>
            <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
              <select
                className="input"
                style={{ fontSize: "12px", padding: "4px 8px" }}
                value={selectedAgentByOrder[order.id] || ""}
                onChange={(e) =>
                  setSelectedAgentByOrder({ ...selectedAgentByOrder, [order.id]: e.target.value })
                }
              >
                <option value="">Select agent...</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.display_name}</option>
                ))}
              </select>
              <button
                className="btn btn-primary"
                style={{ fontSize: "12px", padding: "4px 10px" }}
                onClick={() => handleAssign(order.id)}
                disabled={assigningId === order.id}
              >
                {assigningId === order.id ? "Assigning..." : "Assign"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
