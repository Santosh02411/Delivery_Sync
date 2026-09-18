import React, { useState } from "react";
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator } from "react-native";
import { forgotPassword } from "../services/api";
import { colors } from "../theme";

/**
 * Requests a password reset email — the same POST /auth/forgot-
 * password the web app's ForgotPasswordPage.jsx calls. The email's
 * reset link opens the WEB app (see FRONTEND_URL in backend/app/
 * routes/auth.py), not this one — there's no screen here for actually
 * setting the new password. This is a deliberate scope decision, not
 * an oversight: building real deep-linking (so tapping the emailed
 * link opens THIS app directly on a "set new password" screen) is
 * meaningful additional setup (a registered URL scheme/associated
 * domain, tested on both platforms) for a flow that, realistically,
 * happens rarely per account — an agent can open the emailed link in
 * their phone's browser, set the new password there, then come back
 * here and log in normally. See mobile/README.md's "Password Reset"
 * section.
 */
export default function ForgotPasswordScreen({ onBackToLogin }) {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit() {
    setError("");
    setMessage("");
    setIsSubmitting(true);
    try {
      const result = await forgotPassword(email.trim());
      setMessage(result.message);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.wordmark}>Delivery Sync</Text>

      <View style={styles.card}>
        <Text style={styles.title}>Reset your password</Text>
        <Text style={styles.hint}>
          Enter your account email — we'll send a reset link you can open in your
          phone's browser to set a new password.
        </Text>

        <Text style={styles.label}>Email</Text>
        <TextInput
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          keyboardType="email-address"
          placeholder="you@example.com"
          placeholderTextColor={colors.textMuted}
          editable={!message}
        />

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {message ? <Text style={styles.success}>{message}</Text> : null}

        {!message ? (
          <TouchableOpacity style={styles.button} onPress={handleSubmit} disabled={isSubmitting}>
            {isSubmitting ? <ActivityIndicator color={colors.accentTextOn} /> : <Text style={styles.buttonText}>Send Reset Link</Text>}
          </TouchableOpacity>
        ) : null}

        <TouchableOpacity onPress={onBackToLogin} style={{ marginTop: 18 }}>
          <Text style={styles.switchLink}>Back to log in</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bgPage, justifyContent: "center", padding: 24 },
  wordmark: { color: colors.accent, fontSize: 26, fontWeight: "700", textAlign: "center", marginBottom: 24 },
  card: { backgroundColor: colors.bgSurface, borderRadius: 14, padding: 22, borderWidth: 1, borderColor: colors.border },
  title: { color: colors.textPrimary, fontSize: 17, fontWeight: "700" },
  hint: { color: colors.textSecondary, fontSize: 12.5, marginTop: 8, lineHeight: 18 },
  label: { color: colors.textSecondary, fontSize: 12, marginBottom: 6, marginTop: 16 },
  input: { backgroundColor: colors.bgInput, borderWidth: 1, borderColor: colors.border, borderRadius: 8, padding: 12, color: colors.textPrimary, fontSize: 15 },
  button: { backgroundColor: colors.accent, borderRadius: 8, padding: 14, alignItems: "center", marginTop: 18 },
  buttonText: { color: colors.accentTextOn, fontWeight: "700", fontSize: 15 },
  error: { color: colors.danger, fontSize: 13, marginTop: 14 },
  success: { color: colors.success, fontSize: 13, marginTop: 14, lineHeight: 18 },
  switchLink: { color: colors.accent, fontSize: 13, textAlign: "center", fontWeight: "600" },
});
