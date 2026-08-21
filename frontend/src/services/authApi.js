/**
 * Handles signup/login HTTP calls to the backend's /auth endpoints.
 */

const API_BASE_URL = "http://127.0.0.1:8000";

export async function signupRequest({ username, email, password, role, display_name, org_name, invite_code }) {
  const response = await fetch(`${API_BASE_URL}/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password, role, display_name, org_name, invite_code }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Signup failed.");
  }
  return data; // { access_token, token_type, user, org_invite_code }
}

export async function loginRequest({ username, password }) {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Login failed.");
  }
  return data; // { access_token, user } OR { requires_2fa: true, challenge_token }
}

export async function verifyTwoFactorLoginRequest(challengeToken, code) {
  const response = await fetch(`${API_BASE_URL}/auth/2fa/verify-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ challenge_token: challengeToken, code }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Verification failed.");
  }
  return data; // { access_token, token_type, user }
}

export async function forgotPasswordRequest(email) {
  const response = await fetch(`${API_BASE_URL}/auth/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed.");
  }
  return data; // { message }
}

export async function resetPasswordRequest(token, newPassword) {
  const response = await fetch(`${API_BASE_URL}/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Reset failed.");
  }
  return data; // { message }
}

export async function customerForgotPasswordRequest(email) {
  const response = await fetch(`${API_BASE_URL}/customer/forgot-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed.");
  }
  return data; // { message }
}

export async function customerResetPasswordRequest(token, newPassword) {
  const response = await fetch(`${API_BASE_URL}/customer/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Reset failed.");
  }
  return data; // { message }
}
