import React, { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import {
  fetchTwoFactorStatus,
  setupTwoFactor,
  enableTwoFactor,
  disableTwoFactor,
} from "../services/api";

/**
 * Lets a staff user (agent/dispatcher/admin) turn TOTP-based two-factor
 * authentication on or off for their own account. Available to every
 * role — 2FA protects the login itself, not any one role's permissions.
 *
 * Setup is two steps on purpose: /2fa/setup hands back a QR code (via
 * the same free api.qrserver.com image API already used elsewhere in
 * this app for order QR codes) but doesn't turn anything on yet; only a
 * confirmed code from the freshly-scanned app calls /2fa/enable. This
 * means a setup attempt that's abandoned partway through (never
 * confirmed) can't accidentally lock the account out.
 */
export default function TwoFactorSettings() {
  const { token, updateUser } = useAuth();
  const { showToast } = useToast();

  const [status, setStatus] = useState(null); // { totp_enabled }
  const [isLoading, setIsLoading] = useState(true);

  const [setupData, setSetupData] = useState(null); // { secret, otpauth_uri }
  const [enableCode, setEnableCode] = useState("");
  const [isEnabling, setIsEnabling] = useState(false);

  const [showDisableForm, setShowDisableForm] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");
  const [isDisabling, setIsDisabling] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setIsLoading(true);
    try {
      const data = await fetchTwoFactorStatus(token);
      setStatus(data);
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleStartSetup() {
    try {
      const data = await setupTwoFactor(token);
      setSetupData(data);
      setEnableCode("");
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function handleConfirmEnable(e) {
    e.preventDefault();
    setIsEnabling(true);
    try {
      await enableTwoFactor(token, enableCode.trim());
      showToast("Two-factor authentication is now on.", "success");
      setSetupData(null);
      setEnableCode("");
      updateUser({ totp_enabled: true });
      await load();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setIsEnabling(false);
    }
  }

  async function handleDisable(e) {
    e.preventDefault();
    setIsDisabling(true);
    try {
      await disableTwoFactor(token, disablePassword);
      showToast("Two-factor authentication is now off.", "success");
      setShowDisableForm(false);
      setDisablePassword("");
      updateUser({ totp_enabled: false });
      await load();
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      setIsDisabling(false);
    }
  }

  if (isLoading) return null;

  return (
    <div>
      <h2 className="page-title">Security</h2>

      <div className="card" style={{ maxWidth: "480px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
          <strong style={{ fontSize: "14px" }}>Two-Factor Authentication</strong>
          <span
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: status?.totp_enabled ? "var(--status-delivered)" : "var(--text-secondary)",
            }}
          >
            {status?.totp_enabled ? "Enabled" : "Disabled"}
          </span>
        </div>
        <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", marginBottom: "14px" }}>
          Adds a 6-digit code from an authenticator app (Google Authenticator,
          Authy, 1Password, etc.) as a second step at login, on top of your password.
        </p>

        {status?.totp_enabled && !showDisableForm && (
          <button className="btn-danger-outline" onClick={() => setShowDisableForm(true)}>
            Turn off two-factor authentication
          </button>
        )}

        {status?.totp_enabled && showDisableForm && (
          <form onSubmit={handleDisable}>
            <div className="auth-field">
              <label>Confirm your password to turn it off</label>
              <input
                type="password"
                className="input"
                value={disablePassword}
                onChange={(e) => setDisablePassword(e.target.value)}
                style={{ width: "100%" }}
                required
                autoFocus
              />
            </div>
            <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
              <button type="submit" className="btn-danger-outline" disabled={isDisabling}>
                {isDisabling ? "Turning off..." : "Confirm & Turn Off"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setShowDisableForm(false);
                  setDisablePassword("");
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {!status?.totp_enabled && !setupData && (
          <button className="btn btn-primary" onClick={handleStartSetup}>
            Set up two-factor authentication
          </button>
        )}

        {!status?.totp_enabled && setupData && (
          <div>
            <p style={{ fontSize: "12.5px", marginBottom: "10px" }}>
              Scan this QR code with your authenticator app, then enter the
              6-digit code it shows you to confirm setup.
            </p>
            <img
              src={`https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(setupData.otpauth_uri)}`}
              alt="Two-factor authentication QR code"
              style={{ display: "block", marginBottom: "10px", borderRadius: "6px" }}
            />
            <p style={{ fontSize: "11.5px", color: "var(--text-muted)", marginBottom: "12px" }}>
              Can't scan it? Enter this key manually:{" "}
              <span className="mono" style={{ fontWeight: 600 }}>{setupData.secret}</span>
            </p>
            <form onSubmit={handleConfirmEnable}>
              <div className="auth-field">
                <label>6-digit code</label>
                <input
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  value={enableCode}
                  onChange={(e) => setEnableCode(e.target.value.replace(/\D/g, ""))}
                  required
                  autoFocus
                />
              </div>
              <div style={{ display: "flex", gap: "8px" }}>
                <button type="submit" className="btn btn-primary" disabled={isEnabling || enableCode.length !== 6}>
                  {isEnabling ? "Confirming..." : "Confirm & Enable"}
                </button>
                <button type="button" className="btn" onClick={() => setSetupData(null)}>
                  Cancel
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
