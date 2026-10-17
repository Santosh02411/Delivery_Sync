import React, { useState } from "react";
import PasswordInput from "./PasswordInput";

/**
 * "Delete my account" card shared by the customer Profile and Privacy pages.
 *
 * `onDelete({ password } | { confirm })` must reject with an Error whose
 * message is shown to the user. Accounts that have a password re-enter it;
 * Google-only accounts (no password exists) type `confirmValue` instead
 * (their email), so they're never locked out of deleting their own account.
 */
export default function DeleteAccountCard({ onDelete, needsPassword = true, confirmValue = "", description }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [typed, setTyped] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    if (!window.confirm("This permanently deletes your account. This can't be undone. Continue?")) return;
    setIsDeleting(true);
    try {
      await onDelete(needsPassword ? { password } : { confirm: typed });
    } catch (err) {
      setError(err.message);
      setIsDeleting(false);
    }
  }

  function close() {
    setOpen(false);
    setPassword("");
    setTyped("");
    setError(null);
  }

  return (
    <div className="card" style={{ maxWidth: "500px", borderColor: "var(--danger)" }}>
      <strong style={{ fontSize: "13.5px", color: "var(--danger)" }}>Delete Account</strong>
      <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", margin: "6px 0 12px" }}>
        {description || "Permanently deletes your account and personal data. This can't be undone."}
      </p>

      {!open ? (
        <button className="btn-danger-outline" onClick={() => setOpen(true)}>
          Delete my account…
        </button>
      ) : (
        <form onSubmit={handleSubmit}>
          <div className="auth-field">
            {needsPassword ? (
              <>
                <label htmlFor="delete-account-password">Confirm your password</label>
                <PasswordInput
                  id="delete-account-password"
                  className="input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  style={{ width: "100%" }}
                  required
                  autoFocus
                />
              </>
            ) : (
              <>
                <label htmlFor="delete-account-confirm">Type your email ({confirmValue}) to confirm</label>
                <input
                  id="delete-account-confirm"
                  className="input"
                  type="text"
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  style={{ width: "100%" }}
                  required
                  autoFocus
                />
              </>
            )}
          </div>
          {error && <p role="alert" style={{ color: "var(--danger)", fontSize: "12px" }}>{error}</p>}
          <div style={{ display: "flex", gap: "8px" }}>
            <button type="submit" className="btn-danger-outline" disabled={isDeleting}>
              {isDeleting ? "Deleting..." : "Permanently delete my account"}
            </button>
            <button type="button" className="btn" onClick={close} disabled={isDeleting}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
