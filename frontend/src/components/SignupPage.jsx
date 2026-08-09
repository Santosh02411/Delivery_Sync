import React, { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useCustomerAuth } from "../context/CustomerAuthContext";
import { useTheme } from "../context/ThemeContext";
import "../styles/auth.css";

// Single unified "I am a..." choice — replaces the old two-step
// (Account Type -> then Organization mode -> then role) flow. Each
// value here maps directly to everything the form needs to know:
// whether it's a customer or staff signup, and if staff, whether
// they're joining an org (agent/dispatcher) or creating one (admin).
const ROLE_OPTIONS = [
  { value: "customer", label: "Customer (Track My Orders)" },
  { value: "agent", label: "Delivery Agent" },
  { value: "dispatcher", label: "Dispatcher" },
  { value: "admin", label: "Business / Team Admin (create new organization)" },
];

export default function SignupPage({ onSwitchToLogin, initialAccountType }) {
  const { signup: staffSignup } = useAuth();
  const { signup: customerSignup } = useCustomerAuth();
  const { theme, toggleTheme } = useTheme();

  // initialAccountType may come in as "staff" (from the login page's
  // generic switch) or "customer" — normalize "staff" to "agent" as a
  // reasonable default within the unified dropdown.
  const [iAmA, setIAmA] = useState(
    initialAccountType === "customer" ? "customer" : "agent"
  );
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [username, setUsername] = useState("");
  const [staffEmail, setStaffEmail] = useState("");
  const [staffPassword, setStaffPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [inviteCode, setInviteCode] = useState("");

  const [customerName, setCustomerName] = useState("");
  const [customerEmail, setCustomerEmail] = useState("");
  const [customerPassword, setCustomerPassword] = useState("");

  const isCustomer = iAmA === "customer";
  const isAdmin = iAmA === "admin";
  // agent/dispatcher always join an existing org via invite code;
  // admin always creates a brand new one.
  const isJoiningOrg = iAmA === "agent" || iAmA === "dispatcher";

  async function handleStaffSubmit(e) {
    e.preventDefault();
    setError("");
    setIsSubmitting(true);
    try {
      await staffSignup({
        username,
        email: staffEmail,
        password: staffPassword,
        role: isAdmin ? "agent" : iAmA, // admin role is granted server-side on org creation
        display_name: displayName,
        org_name: isAdmin ? orgName : undefined,
        invite_code: isJoiningOrg ? inviteCode : undefined,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCustomerSubmit(e) {
    e.preventDefault();
    setError("");
    setIsSubmitting(true);
    try {
      await customerSignup(customerEmail, customerPassword, customerName);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="auth-page-wrapper">
      <button className="auth-theme-toggle" onClick={toggleTheme}>
        {theme === "dark" ? "☀ Light" : "☾ Dark"}
      </button>
      <div className="auth-card">
        <h2>Create your account</h2>

        <div className="auth-field">
          <label>I am a...</label>
          <select value={iAmA} onChange={(e) => { setError(""); setIAmA(e.target.value); }}>
            {ROLE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {isCustomer && (
          <>
            <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginBottom: "16px" }}>
              Track every order, from any store using Delivery Sync, in one
              place. Use the same email your orders are placed under so
              they show up automatically.
            </p>
            <form onSubmit={handleCustomerSubmit}>
              <div className="auth-field">
                <label>Name</label>
                <input type="text" value={customerName} onChange={(e) => setCustomerName(e.target.value)} required autoFocus />
              </div>
              <div className="auth-field">
                <label>Email</label>
                <input type="email" value={customerEmail} onChange={(e) => setCustomerEmail(e.target.value)} required />
              </div>
              <div className="auth-field">
                <label>Password</label>
                <input type="password" value={customerPassword} onChange={(e) => setCustomerPassword(e.target.value)} required />
              </div>

              {error && <p className="auth-error">{error}</p>}

              <button type="submit" className="auth-submit-btn" disabled={isSubmitting}>
                {isSubmitting ? "Creating account..." : "Sign up"}
              </button>
            </form>
          </>
        )}

        {!isCustomer && (
          <form onSubmit={handleStaffSubmit}>
            <div className="auth-field">
              <label>Display name</label>
              <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required autoFocus />
            </div>
            <div className="auth-field">
              <label>Username</label>
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} required />
            </div>
            <div className="auth-field">
              <label>Email</label>
              <input type="email" value={staffEmail} onChange={(e) => setStaffEmail(e.target.value)} required />
            </div>
            <div className="auth-field">
              <label>Password</label>
              <input type="password" value={staffPassword} onChange={(e) => setStaffPassword(e.target.value)} required />
            </div>

            {isAdmin && (
              <div className="auth-field">
                <label>Organization Name</label>
                <input type="text" value={orgName} onChange={(e) => setOrgName(e.target.value)} required />
              </div>
            )}

            {isJoiningOrg && (
              <div className="auth-field">
                <label>Invite Code</label>
                <input type="text" value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} required />
              </div>
            )}

            {error && <p className="auth-error">{error}</p>}

            <button type="submit" className="auth-submit-btn" disabled={isSubmitting}>
              {isSubmitting ? "Creating account..." : "Sign up"}
            </button>
          </form>
        )}

        <p className="auth-switch-text">
          Already have an account?{" "}
          <button className="auth-switch-link" onClick={onSwitchToLogin}>
            Log in
          </button>
        </p>
      </div>
    </div>
  );
}
