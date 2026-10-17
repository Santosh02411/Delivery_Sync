import React, { useEffect, useState } from "react";

const RESEND_COOLDOWN_SECONDS = 30;

/**
 * Shown above the dashboard while the account's email isn't verified.
 * Verification is by ONE-TIME CODE: we email a 6-digit code and the person
 * types it here (it auto-submits on the 6th digit). Nothing to click in
 * the email, and it works even if the email is opened on another device.
 *
 * Deliberately not a blocking modal — verification doesn't gate anything
 * else in the app (see routes/auth.py /verify-email). `onVerify(code)`
 * must reject with an Error whose message is shown to the user;
 * `onVerified()` runs after success (to refresh the cached user).
 */
export default function VerificationBanner({ onResend, onVerify, onVerified }) {
  const [code, setCode] = useState("");
  const [status, setStatus] = useState("idle"); // idle | verifying | verified
  const [message, setMessage] = useState(null); // { kind: "error" | "info", text }
  const [dismissed, setDismissed] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const id = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(id);
  }, [cooldown]);

  if (dismissed) return null;

  async function submit(value) {
    setStatus("verifying");
    setMessage(null);
    try {
      await onVerify(value);
      setStatus("verified");
      setMessage({ kind: "info", text: "Email verified. Thank you!" });
      if (onVerified) onVerified();
    } catch (err) {
      setStatus("idle");
      setCode("");
      setMessage({ kind: "error", text: err.message });
    }
  }

  function handleChange(e) {
    const digits = e.target.value.replace(/\D/g, "").slice(0, 6);
    setCode(digits);
    if (digits.length === 6 && status !== "verifying") submit(digits);
  }

  async function handleResend() {
    setMessage(null);
    try {
      await onResend();
      setCooldown(RESEND_COOLDOWN_SECONDS);
      setMessage({ kind: "info", text: "New code sent — check your inbox (and Spam)." });
    } catch (err) {
      setMessage({ kind: "error", text: err.message || "Couldn't send a new code." });
    }
  }

  return (
    <div
      style={{
        padding: "12px 14px",
        marginBottom: "16px",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--border-color)",
        backgroundColor: "var(--bg-surface-elevated)",
        fontSize: "13px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
        <span style={{ flex: 1, minWidth: "220px" }}>
          <strong>Verify your email.</strong> We emailed you a 6-digit code — enter it here.
        </span>
        <input
          className="input"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          aria-label="6-digit verification code"
          placeholder="123456"
          value={code}
          onChange={handleChange}
          disabled={status === "verifying" || status === "verified"}
          maxLength={6}
          style={{ width: "120px", textAlign: "center", letterSpacing: "4px", fontFamily: "monospace", fontSize: "16px" }}
        />
        <button
          className="btn btn-primary"
          style={{ fontSize: "12px", padding: "6px 12px" }}
          onClick={() => submit(code)}
          disabled={code.length !== 6 || status === "verifying" || status === "verified"}
        >
          {status === "verifying" ? "Verifying..." : "Verify"}
        </button>
        <button
          className="btn"
          style={{ fontSize: "12px", padding: "6px 10px" }}
          onClick={handleResend}
          disabled={cooldown > 0 || status === "verified"}
        >
          {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
        </button>
        <button className="btn" style={{ fontSize: "12px", padding: "6px 10px" }} onClick={() => setDismissed(true)}>
          Dismiss
        </button>
      </div>
      {message && (
        <p
          role={message.kind === "error" ? "alert" : "status"}
          style={{ margin: "8px 0 0", fontSize: "12px", color: message.kind === "error" ? "var(--danger)" : "var(--status-delivered)" }}
        >
          {message.text}
        </p>
      )}
    </div>
  );
}
