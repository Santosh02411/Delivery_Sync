import React, { useState } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { CustomerAuthProvider, useCustomerAuth } from "./context/CustomerAuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ThemeProvider } from "./context/ThemeContext";
import ConnectivityBanner from "./components/ConnectivityBanner";
import Sidebar from "./components/Sidebar";
import AgentDeliveryList from "./components/AgentDeliveryList";
import AgentPerformance from "./components/AgentPerformance";
import DispatcherTable from "./components/DispatcherTable";
import AdminPanel from "./components/AdminPanel";
import LoginPage from "./components/LoginPage";
import SignupPage from "./components/SignupPage";
import ForgotPasswordPage from "./components/ForgotPasswordPage";
import ResetPasswordPage from "./components/ResetPasswordPage";
import TrackingPage from "./components/TrackingPage";
import CustomerDashboard from "./components/CustomerDashboard";

function StaffDashboard({ user }) {
  const [activeView, setActiveView] = useState(null);
  const currentView = activeView || (user.role === "agent" ? "deliveries" : "dashboard");

  return (
    <div className="app-shell">
      <Sidebar activeView={currentView} onNavigate={setActiveView} />
      <div className="main-content">
        <ConnectivityBanner />
        <div style={{ marginTop: "20px" }}>
          {user.role === "agent" && currentView === "deliveries" && <AgentDeliveryList />}
          {user.role === "agent" && currentView === "performance" && <AgentPerformance />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "dashboard" && (
            <DispatcherTable />
          )}
          {user.role === "admin" && currentView === "admin" && <AdminPanel />}
        </div>
      </div>
    </div>
  );
}

function AuthFlow() {
  const [view, setView] = useState("login");
  const [signupAccountType, setSignupAccountType] = useState("staff");

  if (view === "login") {
    return (
      <LoginPage
        onSwitchToSignup={(accountType) => {
          setSignupAccountType(accountType);
          setView("signup");
        }}
        onForgotPassword={() => setView("forgot-password")}
      />
    );
  }
  if (view === "forgot-password") {
    return <ForgotPasswordPage onBackToLogin={() => setView("login")} />;
  }
  return (
    <SignupPage
      onSwitchToLogin={() => setView("login")}
      initialAccountType={signupAccountType}
    />
  );
}

function RootRouter() {
  const { user, isLoading: staffLoading } = useAuth();
  const { customer, isLoading: customerLoading } = useCustomerAuth();

  const urlParams = new URLSearchParams(window.location.search);
  const resetToken = urlParams.get("reset_token");
  const trackId = urlParams.get("track");

  if (trackId) return <TrackingPage deliveryId={trackId} />;

  if (resetToken) {
    return (
      <ResetPasswordPage
        token={resetToken}
        onDone={() => {
          window.history.replaceState({}, "", window.location.pathname);
          window.location.reload();
        }}
      />
    );
  }

  if (staffLoading || customerLoading) return null;

  if (user) return <StaffDashboard user={user} />;
  if (customer) return <CustomerDashboard />;

  return <AuthFlow />;
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <CustomerAuthProvider>
          <ToastProvider>
            <RootRouter />
          </ToastProvider>
        </CustomerAuthProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
