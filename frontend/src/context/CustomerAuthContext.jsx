import React, { createContext, useContext, useState, useEffect } from "react";

const CustomerAuthContext = createContext(null);
const STORAGE_KEY = "delivery_sync_customer_auth";
const API_BASE_URL = "http://127.0.0.1:8000";

/**
 * Customer session state — completely separate from the staff
 * AuthContext (different localStorage key, different backend endpoints,
 * different token shape). A customer and a staff member could even be
 * logged in simultaneously in two different tabs without interfering
 * with each other.
 */
export function CustomerAuthProvider({ children }) {
  const [customer, setCustomer] = useState(null);
  const [token, setToken] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      setCustomer(parsed.customer);
      setToken(parsed.token);
    }
    setIsLoading(false);
  }, []);

  function persistSession(newToken, newCustomer) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: newToken, customer: newCustomer }));
    setToken(newToken);
    setCustomer(newCustomer);
  }

  async function signup(email, password, name) {
    const response = await fetch(`${API_BASE_URL}/customer/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Signup failed.");
    persistSession(data.access_token, data.customer);
    return data.customer;
  }

  async function login(email, password) {
    const response = await fetch(`${API_BASE_URL}/customer/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Login failed.");
    persistSession(data.access_token, data.customer);
    return data.customer;
  }

  function logout() {
    localStorage.removeItem(STORAGE_KEY);
    setCustomer(null);
    setToken(null);
  }

  function updateCustomer(patch) {
    setCustomer((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...parsed, customer: next }));
      }
      return next;
    });
  }

  return (
    <CustomerAuthContext.Provider value={{ customer, token, isLoading, signup, login, logout, updateCustomer }}>
      {children}
    </CustomerAuthContext.Provider>
  );
}

export function useCustomerAuth() {
  const context = useContext(CustomerAuthContext);
  if (!context) {
    throw new Error("useCustomerAuth must be used within a CustomerAuthProvider");
  }
  return context;
}
