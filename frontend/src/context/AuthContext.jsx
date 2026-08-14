import React, { createContext, useContext, useState, useEffect } from "react";
import { signupRequest, loginRequest, verifyTwoFactorLoginRequest } from "../services/authApi";

const AuthContext = createContext(null);

const STORAGE_KEY = "delivery_sync_auth";

/**
 * Provides authentication state (current user, token) and actions
 * (login, signup, logout) to the whole app.
 *
 * Session is persisted to localStorage so refreshing the page doesn't log
 * the user out — only explicit logout, or the token expiring server-side
 * (24 hours), ends the session.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      setUser(parsed.user);
      setToken(parsed.token);
    }
    setIsLoading(false);
  }, []);

  function persistSession(newToken, newUser) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: newToken, user: newUser }));
    setToken(newToken);
    setUser(newUser);
  }

  async function login(username, password) {
    const data = await loginRequest({ username, password });
    if (data.requires_2fa) {
      // Password was correct, but this account has 2FA on — hand back
      // the challenge token so LoginPage can prompt for a code next,
      // instead of persisting a session that doesn't exist yet.
      return { requires_2fa: true, challenge_token: data.challenge_token };
    }
    persistSession(data.access_token, data.user);
    return data.user;
  }

  async function completeTwoFactorLogin(challengeToken, code) {
    const data = await verifyTwoFactorLoginRequest(challengeToken, code);
    persistSession(data.access_token, data.user);
    return data.user;
  }

  async function signup({ username, email, password, role, display_name, org_name, invite_code }) {
    const data = await signupRequest({ username, email, password, role, display_name, org_name, invite_code });
    persistSession(data.access_token, data.user);
    return data; // includes org_invite_code when a NEW org was just created
  }

  function logout() {
    localStorage.removeItem(STORAGE_KEY);
    setUser(null);
    setToken(null);
  }

  function updateUser(patch) {
    setUser((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...parsed, user: next }));
      }
      return next;
    });
  }

  const value = { user, token, isLoading, login, completeTwoFactorLogin, signup, logout, updateUser };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
