import React, { useEffect, useState } from "react";
import { v4 as uuidv4 } from "uuid";
import {
  fetchAllDeliveriesFromServer,
  fetchAgentsList,
  createDeliveryOnServer,
} from "../services/api";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import DeliveryDetailModal from "./DeliveryDetailModal";

const STATUS_FILTER_OPTIONS = [
  { value: "all", label: "All Statuses" },
  { value: "picked_up", label: "Picked Up" },
  { value: "out_for_delivery", label: "Out for Delivery" },
  { value: "delivered", label: "Delivered" },
  { value: "failed_attempt", label: "Failed Attempt" },
];

const SORT_OPTIONS = [
  { value: "updated_desc", label: "Most Recently Updated" },
  { value: "updated_asc", label: "Oldest Updated First" },
  { value: "order_id_asc", label: "Order ID (A-Z)" },
  { value: "status", label: "Status" },
];

/**
 * Dashboard for dispatchers/managers — assign new deliveries, and browse
 * all deliveries with status filtering, agent filtering, a date range
 * filter, sorting, search, and a click-through detail modal per delivery.
 */
export default function DispatcherTable() {
  const { token } = useAuth();
  const { showToast } = useToast();
  const [deliveries, setDeliveries] = useState([]);
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortOption, setSortOption] = useState("updated_desc");

  // Assignment form state
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [newOrderId, setNewOrderId] = useState("");
  const [newNotes, setNewNotes] = useState("");
  const [isAssigning, setIsAssigning] = useState(false);

  // Detail modal
  const [selectedDelivery, setSelectedDelivery] = useState(null);

  useEffect(() => {
    loadDeliveries();
    loadAgents();
  }, []);

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

  async function handleAssignDelivery(e) {
    e.preventDefault();

    if (!selectedAgentId || !newOrderId.trim()) {
      showToast("Pick an agent and enter an order ID.", "error");
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
        created_at: now,
        updated_at: now,
      });
      showToast(`Assigned ${newOrderId} successfully.`, "success");
      setNewOrderId("");
      setNewNotes("");
      await loadDeliveries();
    } catch (err) {
      showToast(`Failed to assign: ${err.message}`, "error");
    } finally {
      setIsAssigning(false);
    }
  }

  const agentNameById = new Map(agents.map((a) => [a.id, a.display_name]));

  // ---- Summary stats ----
  const today = new Date().toDateString();
  const statCounts = {
    picked_up: 0,
    out_for_delivery: 0,
    delivered: 0,
    failed_attempt: 0,
  };
  let deliveredToday = 0;
  for (const d of deliveries) {
    if (statCounts[d.status] !== undefined) statCounts[d.status] += 1;
    if (
      d.status === "delivered" &&
      new Date(d.updated_at).toDateString() === today
    ) {
      deliveredToday += 1;
    }
  }

  // ---- Filter ----
  let visibleDeliveries = deliveries.filter((d) => {
    const matchesStatus = statusFilter === "all" || d.status === statusFilter;
    const matchesAgent = agentFilter === "all" || d.agent_id === agentFilter;

    const updatedDate = new Date(d.updated_at);
    const matchesFrom = !dateFrom || updatedDate >= new Date(dateFrom);
    // Add a day to "to" so the selected end date is inclusive of that whole day
    const matchesTo = !dateTo || updatedDate <= new Date(dateTo + "T23:59:59");

    const agentName = agentNameById.get(d.agent_id) || d.agent_id;
    const query = searchQuery.toLowerCase();
    const matchesSearch =
      query === "" ||
      d.order_id.toLowerCase().includes(query) ||
      agentName.toLowerCase().includes(query);

    return (
      matchesStatus && matchesAgent && matchesFrom && matchesTo && matchesSearch
    );
  });

  // ---- Sort ----
  visibleDeliveries = [...visibleDeliveries].sort((a, b) => {
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

  function clearFilters() {
    setStatusFilter("all");
    setAgentFilter("all");
    setDateFrom("");
    setDateTo("");
    setSearchQuery("");
  }

  return (
    <div style={{ padding: "16px" }}>
      <h2>Dispatcher Dashboard</h2>

      {/* Summary stat cards */}
      <div
        style={{
          display: "flex",
          gap: "12px",
          marginBottom: "24px",
          flexWrap: "wrap",
        }}
      >
        <StatCard
          label="Picked Up"
          value={statCounts.picked_up}
          color="#1565c0"
        />
        <StatCard
          label="Out for Delivery"
          value={statCounts.out_for_delivery}
          color="#f9a825"
        />
        <StatCard
          label="Delivered"
          value={statCounts.delivered}
          color="#2e7d32"
        />
        <StatCard
          label="Failed Attempts"
          value={statCounts.failed_attempt}
          color="#c62828"
        />
        <StatCard
          label="Delivered Today"
          value={deliveredToday}
          color="#00897b"
        />
      </div>

      {/* Assignment form */}
      <div
        style={{
          border: "1px solid #ddd",
          borderRadius: "8px",
          padding: "16px",
          marginBottom: "24px",
          backgroundColor: "#fafafa",
        }}
      >
        <h3 style={{ marginTop: 0 }}>Assign a New Delivery</h3>
        {agents.length === 0 && (
          <p style={{ color: "#666" }}>
            No agents registered yet. Have an agent sign up first.
          </p>
        )}
        {agents.length > 0 && (
          <form
            onSubmit={handleAssignDelivery}
            style={{
              display: "flex",
              gap: "8px",
              flexWrap: "wrap",
              alignItems: "flex-end",
            }}
          >
            <div>
              <label style={{ display: "block", fontSize: "12px" }}>
                Agent
              </label>
              <select
                value={selectedAgentId}
                onChange={(e) => setSelectedAgentId(e.target.value)}
                style={{ padding: "6px" }}
              >
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.display_name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: "block", fontSize: "12px" }}>
                Order ID
              </label>
              <input
                type="text"
                value={newOrderId}
                onChange={(e) => setNewOrderId(e.target.value)}
                placeholder="order-123"
                style={{ padding: "6px" }}
              />
            </div>
            <div style={{ flexGrow: 1, minWidth: "150px" }}>
              <label style={{ display: "block", fontSize: "12px" }}>
                Notes (optional)
              </label>
              <input
                type="text"
                value={newNotes}
                onChange={(e) => setNewNotes(e.target.value)}
                placeholder="Fragile, deliver before 5pm..."
                style={{ padding: "6px", width: "100%" }}
              />
            </div>
            <button type="submit" disabled={isAssigning}>
              {isAssigning ? "Assigning..." : "Assign"}
            </button>
          </form>
        )}
      </div>

      {/* Filters, sort, search */}
      <div
        style={{
          border: "1px solid #eee",
          borderRadius: "8px",
          padding: "12px",
          marginBottom: "16px",
          display: "flex",
          gap: "12px",
          flexWrap: "wrap",
          alignItems: "flex-end",
        }}
      >
        <button onClick={loadDeliveries}>Refresh</button>

        <div>
          <label style={{ display: "block", fontSize: "12px" }}>Status</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label style={{ display: "block", fontSize: "12px" }}>Agent</label>
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
          >
            <option value="all">All Agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label style={{ display: "block", fontSize: "12px" }}>From</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </div>

        <div>
          <label style={{ display: "block", fontSize: "12px" }}>To</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </div>

        <div>
          <label style={{ display: "block", fontSize: "12px" }}>Sort by</label>
          <select
            value={sortOption}
            onChange={(e) => setSortOption(e.target.value)}
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>

        <input
          type="text"
          placeholder="Search by order ID or agent name..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ padding: "6px 10px", flexGrow: 1, minWidth: "200px" }}
        />

        <button
          onClick={clearFilters}
          style={{
            background: "none",
            border: "1px solid #999",
            borderRadius: "4px",
          }}
        >
          Clear Filters
        </button>

        <span style={{ color: "#666" }}>
          Showing {visibleDeliveries.length} of {deliveries.length}
        </span>
      </div>

      {error && <p style={{ color: "red" }}>{error}</p>}

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={cellStyle}>Order ID</th>
            <th style={cellStyle}>Agent</th>
            <th style={cellStyle}>Status</th>
            <th style={cellStyle}>Last Updated</th>
          </tr>
        </thead>
        <tbody>
          {visibleDeliveries.map((d) => (
            <tr
              key={d.id}
              onClick={() => setSelectedDelivery(d)}
              style={{ cursor: "pointer" }}
              onMouseEnter={(e) =>
                (e.currentTarget.style.backgroundColor = "#f5f5f5")
              }
              onMouseLeave={(e) =>
                (e.currentTarget.style.backgroundColor = "transparent")
              }
            >
              <td style={cellStyle}>{d.order_id}</td>
              <td style={cellStyle}>
                {agentNameById.get(d.agent_id) || d.agent_id}
              </td>
              <td style={cellStyle}>{d.status}</td>
              <td style={cellStyle}>
                {new Date(d.updated_at).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {visibleDeliveries.length === 0 && (
        <p style={{ color: "#666", marginTop: "12px" }}>
          No deliveries match this filter/search.
        </p>
      )}

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

const cellStyle = {
  border: "1px solid #ddd",
  padding: "8px",
  textAlign: "left",
};

function StatCard({ label, value, color }) {
  return (
    <div
      style={{
        border: "1px solid #ddd",
        borderRadius: "8px",
        padding: "12px 16px",
        minWidth: "130px",
        borderTop: `3px solid ${color}`,
      }}
    >
      <div style={{ fontSize: "24px", fontWeight: "bold" }}>{value}</div>
      <div style={{ fontSize: "12px", color: "#666" }}>{label}</div>
    </div>
  );
}
