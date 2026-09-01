import React, { useState, useEffect } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { CustomerAuthProvider, useCustomerAuth } from "./context/CustomerAuthContext";
import { ToastProvider, useToast } from "./context/ToastContext";
import { ThemeProvider } from "./context/ThemeContext";
import { resendVerificationRequest, customerResendVerificationRequest } from "./services/authApi";
import ConnectivityBanner from "./components/ConnectivityBanner";
import VerificationBanner from "./components/VerificationBanner";
import Sidebar from "./components/Sidebar";
import AgentDeliveryList from "./components/AgentDeliveryList";
import AgentPerformance from "./components/AgentPerformance";
import DispatcherTable from "./components/DispatcherTable";
import ProductManager from "./components/ProductManager";
import AnalyticsDashboard from "./components/AnalyticsDashboard";
import AdvancedAnalyticsPanel from "./components/AdvancedAnalyticsPanel";
import AdminPanel from "./components/AdminPanel";
import AuditLogViewer from "./components/AuditLogViewer";
import ZoneManager from "./components/ZoneManager";
import FailedDeliveryReasonManager from "./components/FailedDeliveryReasonManager";
import MyWorkforce from "./components/MyWorkforce";
import WorkforceManager from "./components/WorkforceManager";
import ReturnRequestsPanel from "./components/ReturnRequestsPanel";
import TwoFactorSettings from "./components/TwoFactorSettings";
import SecurityDashboard from "./components/SecurityDashboard";
import AccountSettings from "./components/AccountSettings";
import LoginPage from "./components/LoginPage";
import SignupPage from "./components/SignupPage";
import ForgotPasswordPage from "./components/ForgotPasswordPage";
import ResetPasswordPage from "./components/ResetPasswordPage";
import VerifyEmailPage from "./components/VerifyEmailPage";
import TrackingPage from "./components/TrackingPage";
import CustomerDashboard from "./components/CustomerDashboard";
import SlaManager from "./components/SlaManager";
import PodSettingsPanel from "./components/PodSettingsPanel";
import WarehouseManager from "./components/WarehouseManager";
import FleetManager from "./components/FleetManager";
import SupportManager from "./components/SupportManager";
import FinanceManager from "./components/FinanceManager";
import ApiWebhooksManager from "./components/ApiWebhooksManager";
import OrganizationSettings from "./components/OrganizationSettings";
import RbacManager from "./components/RbacManager";
import ReconciliationDashboard from "./components/ReconciliationDashboard";
import RtoManager from "./components/RtoManager";
import RoutingInsights from "./components/RoutingInsights";
import NotificationTemplateManager from "./components/NotificationTemplateManager";

function StaffDashboard({ user }) {
  const { token } = useAuth();
  const [activeView, setActiveView] = useState(null);
  const currentView = activeView || (user.role === "agent" ? "deliveries" : "dashboard");

  return (
    <div className="app-shell">
      <Sidebar activeView={currentView} onNavigate={setActiveView} />
      <div className="main-content">
        <ConnectivityBanner />
        <div style={{ marginTop: "20px" }}>
          {!user.email_verified && (
            <VerificationBanner onResend={() => resendVerificationRequest(token)} />
          )}
          {user.role === "agent" && currentView === "deliveries" && <AgentDeliveryList />}
          {user.role === "agent" && currentView === "performance" && <AgentPerformance />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "dashboard" && (
            <DispatcherTable />
          )}
          {user.role === "admin" && currentView === "admin" && <AdminPanel />}
          {user.role === "admin" && currentView === "zones" && <ZoneManager />}
          {user.role === "admin" && currentView === "reason-codes" && <FailedDeliveryReasonManager />}
          {user.role === "agent" && currentView === "workforce" && <MyWorkforce />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "workforce" && <WorkforceManager />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "returns" && (
            <ReturnRequestsPanel />
          )}
          {user.role === "admin" && currentView === "audit-log" && <AuditLogViewer />}
          {user.role === "admin" && currentView === "analytics" && <AnalyticsDashboard />}
          {user.role === "admin" && currentView === "advanced-analytics" && <AdvancedAnalyticsPanel />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "products" && (
            <ProductManager />
          )}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "sla" && <SlaManager />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "warehouses" && <WarehouseManager />}
          {currentView === "fleet" && (user.role === "dispatcher" || user.role === "admin" || user.role === "agent") && <FleetManager />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "support" && <SupportManager />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "invoicing" && <FinanceManager />}
          {user.role === "admin" && currentView === "api-webhooks" && <ApiWebhooksManager />}
          {user.role === "admin" && currentView === "organization" && <OrganizationSettings />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "reconciliation" && <ReconciliationDashboard />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "rto" && <RtoManager />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "routing" && <RoutingInsights />}
          {(user.role === "dispatcher" || user.role === "admin") && currentView === "notification-templates" && <NotificationTemplateManager />}
          {user.role === "admin" && currentView === "pod-settings" && <PodSettingsPanel />}
          {user.role === "admin" && currentView === "rbac" && <RbacManager />}
          {currentView === "account" && <AccountSettings />}
          {currentView === "security" && (
            <div>
              <TwoFactorSettings />
              <SecurityDashboard />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AuthFlow() {
  const [view, setView] = useState("login");
  const [signupAccountType, setSignupAccountType] = useState("staff");
  const [forgotPasswordAccountType, setForgotPasswordAccountType] = useState("staff");

  if (view === "login") {
    return (
      <LoginPage
        onSwitchToSignup={(accountType) => {
          setSignupAccountType(accountType);
          setView("signup");
        }}
        onForgotPassword={(accountType) => {
          setForgotPasswordAccountType(accountType);
          setView("forgot-password");
        }}
      />
    );
  }
  if (view === "forgot-password") {
    return <ForgotPasswordPage onBackToLogin={() => setView("login")} accountType={forgotPasswordAccountType} />;
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
  const { showToast } = useToast();

  useEffect(() => {
    // Surfaces a real Background Sync completion (see public/sw.js) —
    // including one that happened while every tab was closed, the
    // moment this tab is next open to show it.
    function handleBackgroundSync(event) {
      showToast(`Synced ${event.detail.syncedCount} item(s) that were queued while offline.`, "info");
    }
    window.addEventListener("background-sync-complete", handleBackgroundSync);
    return () => window.removeEventListener("background-sync-complete", handleBackgroundSync);
  }, [showToast]);

  const urlParams = new URLSearchParams(window.location.search);
  const resetToken = urlParams.get("reset_token");
  const customerResetToken = urlParams.get("customer_reset_token");
  const verifyEmailToken = urlParams.get("verify_email_token");
  const verifyCustomerEmailToken = urlParams.get("verify_customer_email_token");
  const trackId = urlParams.get("track");

  if (trackId) return <TrackingPage deliveryId={trackId} />;

  if (resetToken || customerResetToken) {
    return (
      <ResetPasswordPage
        token={resetToken || customerResetToken}
        accountType={customerResetToken ? "customer" : "staff"}
        onDone={() => {
          window.history.replaceState({}, "", window.location.pathname);
          window.location.reload();
        }}
      />
    );
  }

  if (verifyEmailToken || verifyCustomerEmailToken) {
    return (
      <VerifyEmailPage
        token={verifyEmailToken || verifyCustomerEmailToken}
        accountType={verifyCustomerEmailToken ? "customer" : "staff"}
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
