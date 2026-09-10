import React, { createContext, useContext, useEffect, useState } from "react";
import * as api from "../services/api";
import { stopBackgroundLocationTracking } from "../locationTask";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const token = await api.getAccessToken();
      if (token) {
        try {
          const profile = await api.fetchMyProfile();
          setUser(profile);
        } catch (err) {
          // Expired/invalid token — same "fall back to logged out"
          // behavior as the web app's own AuthContext.
          await api.clearTokens();
        }
      }
      setIsLoading(false);
    })();
  }, []);

  async function login(username, password) {
    const data = await api.login(username, password);
    if (data.requires_2fa) {
      return data; // { requires_2fa: true, challenge_token } — LoginScreen handles the next step
    }
    await api.saveTokens(data.access_token, data.refresh_token);
    setUser(data.user);
    return data;
  }

  async function completeTwoFactorLogin(challengeToken, code) {
    const data = await api.verifyTwoFactorLogin(challengeToken, code);
    await api.saveTokens(data.access_token, data.refresh_token);
    setUser(data.user);
    return data.user;
  }

  async function logout() {
    await stopBackgroundLocationTracking();
    await api.clearTokens();
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, isLoading, login, completeTwoFactorLogin, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
