import React, { useEffect } from "react";
import { View, ActivityIndicator, StyleSheet } from "react-native";
import { AuthProvider, useAuth } from "./src/context/AuthContext";
import LoginScreen from "./src/screens/LoginScreen";
import AppNavigator from "./src/AppNavigator";
import { colors } from "./src/theme";
import { startAutoSync } from "./src/services/offlineSync";

// Registers the background location task (see src/locationTask.js) as
// a side effect of import — TaskManager.defineTask() must run once at
// module load time, before any screen tries to start/stop it, exactly
// like Expo's own docs specify.
import "./src/locationTask";

function Root() {
  const { user, isLoading } = useAuth();

  // Only runs once a real, logged-in user is known — the sync engine
  // has nothing to authenticate as before that, and (more importantly)
  // offlineStore's setActiveUser(profile.id) must have already run
  // (see AuthContext.js's applyUser) before any pending-queue read is
  // safe to attempt.
  useEffect(() => {
    if (!user) return undefined;
    const stopAutoSync = startAutoSync();
    return stopAutoSync;
  }, [user?.id]);

  if (isLoading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  return user ? <AppNavigator /> : <LoginScreen />;
}

export default function App() {
  return (
    <AuthProvider>
      <Root />
    </AuthProvider>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, backgroundColor: colors.bgPage, justifyContent: "center", alignItems: "center" },
});
