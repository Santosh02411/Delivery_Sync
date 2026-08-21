import React, { useState } from "react";
import { resetPasswordRequest, customerResetPasswordRequest } from "../services/authApi";
import { useTheme } from "../context/ThemeContext";
import "../styles/auth.css";

/**
 * Shown when the app loads with a ?reset_token=... (staff) or
 * ?customer_reset_token=... (customer) in the URL (i.e. the person
 * clicked the link from their "reset email" — which, without SMTP
 * configured, means the link printed to the backend console during local
 * development/testing).
 */
export default function ResetPasswordPage({ token, onDone, accountType = "staff" }) {
  const { theme, toggleTheme } = useTheme();
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isCustomer = accountType === "customer";

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");

    if (newPassword !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }

    setIsSubmitting(true);
    try {
      const result = isCustomer
        ? await customerResetPasswordRequest(token, newPassword)
        : await resetPasswordRequest(token, newPassword);
      setMessage(result.message);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="auth-page-wrapper">
            <div className="auth-wordmark">Delivery Sync</div>
      <button className="auth-theme-toggle" onClick={toggleTheme}>
        {theme === "dark" ? "☀ Light" : "☾ Dark"}
      </button>
      <div className="auth-card">
        <h2>Set a new password</h2>

        {!message && (
          <form onSubmit={handleSubmit}>
            <div className="auth-field">
              <label>New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="auth-field">
              <label>Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
            </div>

            {error && <p className="auth-error">{error}</p>}

            <button type="submit" className="auth-submit-btn" disabled={isSubmitting}>
              {isSubmitting ? "Resetting..." : "Reset Password"}
            </button>
          </form>
        )}

        {message && (
          <>
            <p style={{ fontSize: "13.5px", color: "var(--status-delivered)", marginBottom: "16px" }}>
              {message}
            </p>
            <button className="auth-submit-btn" onClick={onDone}>
              Continue to Log In
            </button>
          </>
        )}
      </div>
    </div>
  );
}
