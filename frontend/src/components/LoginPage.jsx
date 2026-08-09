import React, { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useCustomerAuth } from "../context/CustomerAuthContext";
import { useTheme } from "../context/ThemeContext";
import "../styles/auth.css";

export default function LoginPage({ onSwitchToSignup, onForgotPassword }) {
  const { login: staffLogin } = useAuth();
  const { login: customerLogin } = useCustomerAuth();
  const { theme, toggleTheme } = useTheme();

  const [accountType, setAccountType] = useState("staff"); // "staff" | "customer"
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setIsSubmitting(true);
    try {
      if (accountType === "staff") {
        await staffLogin(identifier, password);
      } else {
        await customerLogin(identifier, password);
      }
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
        <h2>Log in</h2>

        <div className="auth-field">
          <label>I am a...</label>
          <select value={accountType} onChange={(e) => { setAccountType(e.target.value); setIdentifier(""); }}>
            <option value="staff">Delivery Agent / Dispatcher / Admin</option>
            <option value="customer">Customer (Track My Orders)</option>
          </select>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="auth-field">
            <label>{accountType === "staff" ? "Username" : "Email"}</label>
            <input
              type={accountType === "staff" ? "text" : "email"}
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="auth-field">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && <p className="auth-error">{error}</p>}

          <button type="submit" className="auth-submit-btn" disabled={isSubmitting}>
            {isSubmitting ? "Logging in..." : "Log in"}
          </button>
        </form>

        {accountType === "staff" && (
          <p className="auth-switch-text">
            <button className="auth-switch-link" onClick={onForgotPassword}>
              Forgot password?
            </button>
          </p>
        )}

        <p className="auth-switch-text">
          Don't have an account?{" "}
          <button className="auth-switch-link" onClick={() => onSwitchToSignup(accountType)}>
            Sign up
          </button>
        </p>
      </div>
    </div>
  );
}
