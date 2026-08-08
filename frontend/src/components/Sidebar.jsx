import React, { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useTheme } from "../context/ThemeContext";

/**
 * Left sidebar navigation. Nav items differ by role: agents get
 * "My Deliveries" and "Performance"; dispatchers get a single
 * "Dashboard" (which already includes assignment, filtering, and stats).
 *
 * On mobile (see the max-width:768px block in theme.css), this becomes an
 * off-canvas drawer: hidden by default, toggled open by a hamburger button
 * in a slim top bar, with a dark overlay behind it that closes the drawer
 * when tapped. On desktop, none of that applies — the sidebar is simply
 * always visible, exactly as before.
 */
export default function Sidebar({ activeView, onNavigate }) {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [isMobileOpen, setIsMobileOpen] = useState(false);

  const agentLinks = [
    { key: "deliveries", label: "My Deliveries", icon: "\u25A4" },
    { key: "performance", label: "Performance", icon: "\u25CE" },
  ];

  const dispatcherLinks = [{ key: "dashboard", label: "Dashboard", icon: "\u25A4" }];

  // Admins get BOTH links — the backend grants admins full dispatcher
  // permissions (assigning deliveries, viewing the dashboard, exporting
  // CSVs), on top of user management. Without "Dashboard" here, an admin
  // — including the very first user of any new organization — would have
  // no way to actually assign or manage deliveries at all.
  const adminLinks = [
    { key: "dashboard", label: "Dashboard", icon: "\u25A4" },
    { key: "admin", label: "Manage Users", icon: "\u2699" },
  ];

  const links =
    user.role === "agent" ? agentLinks :
    user.role === "admin" ? adminLinks :
    dispatcherLinks;

  const sectionLabel =
    user.role === "agent" ? "Agent" :
    user.role === "admin" ? "Admin" :
    "Dispatch";

  function handleNavigate(key) {
    onNavigate(key);
    setIsMobileOpen(false); // close the drawer after picking a page, on mobile
  }

  return (
    <>
      {/* Only visible on mobile widths (theme.css) — always present in the
          DOM so the hamburger button exists regardless of screen size at
          the moment the component first renders. */}
      <div className="mobile-topbar">
        <span className="mobile-topbar-brand">Delivery Sync</span>
        <button
          className="mobile-menu-btn"
          onClick={() => setIsMobileOpen(!isMobileOpen)}
          aria-label="Toggle menu"
        >
          {isMobileOpen ? "\u2715" : "\u2630"}
        </button>
      </div>

      {isMobileOpen && (
        <div className="sidebar-overlay" onClick={() => setIsMobileOpen(false)} />
      )}

      <div className={`sidebar ${isMobileOpen ? "mobile-open" : ""}`}>
        <div className="sidebar-brand">Delivery Sync</div>

        <div className="sidebar-section-label">{sectionLabel}</div>

        {links.map((link) => (
          <div
            key={link.key}
            className={`sidebar-link ${activeView === link.key ? "active" : ""}`}
            onClick={() => handleNavigate(link.key)}
          >
            <span aria-hidden="true">{link.icon}</span>
            {link.label}
          </div>
        ))}

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <strong>{user.display_name}</strong>
            {user.role}
          </div>
          <button className="btn sidebar-action-btn" onClick={toggleTheme}>
            {theme === "dark" ? "☀ Light Mode" : "☾ Dark Mode"}
          </button>
          <button className="btn sidebar-action-btn" onClick={logout}>
            Log out
          </button>
        </div>
      </div>
    </>
  );
}
