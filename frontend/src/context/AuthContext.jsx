import React, { createContext, useContext, useState, useEffect } from "react";
import { signupRequest, loginRequest } from "../services/authApi";

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
    persistSession(data.access_token, data.user);
    return data.user;
  }

  async function signup({ username, password, role, display_name }) {
    const data = await signupRequest({ username, password, role, display_name });
    persistSession(data.access_token, data.user);
    return data.user;
  }

  function logout() {
    localStorage.removeItem(STORAGE_KEY);
    setUser(null);
    setToken(null);
  }

  const value = { user, token, isLoading, login, signup, logout };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
