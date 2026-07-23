import React, { useState } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import ConnectivityBanner from "./components/ConnectivityBanner";
import AgentDeliveryList from "./components/AgentDeliveryList";
import DispatcherTable from "./components/DispatcherTable";
import LoginPage from "./components/LoginPage";
import SignupPage from "./components/SignupPage";

function AppShell() {
  const { user, isLoading, logout } = useAuth();
  const [authView, setAuthView] = useState("login"); // "login" or "signup"

  if (isLoading) {
    return null; // avoid a login-page flash while checking localStorage
  }

  if (!user) {
    return authView === "login" ? (
      <LoginPage onSwitchToSignup={() => setAuthView("signup")} />
    ) : (
      <SignupPage onSwitchToLogin={() => setAuthView("login")} />
    );
  }

  return (
    <div>
      <ConnectivityBanner />

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "12px 16px",
          borderBottom: "1px solid #ddd",
        }}
      >
        <span>
          Logged in as <strong>{user.display_name}</strong> ({user.role})
        </span>
        <button onClick={logout}>Log out</button>
      </div>

      {user.role === "agent" ? <AgentDeliveryList /> : <DispatcherTable />}
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}
