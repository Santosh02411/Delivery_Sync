import React, { useCallback, useEffect, useState } from "react";
import { View, Text, FlatList, TouchableOpacity, StyleSheet, RefreshControl, Alert } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { fetchMyDeliveries } from "../services/api";
import { getCachedDeliveries } from "../services/offlineStore";
import { getSyncingIds, subscribeSyncState, retryBlocked, discardBlocked, acknowledgeConflictNotice, runSync } from "../services/offlineSync";
import { mergeLocalSyncState } from "../services/syncView";
import { summarizeSyncStates, describeSyncProblem } from "../services/syncOutcome";
import SyncStatusPanel from "../components/SyncStatusPanel";
import { useAuth } from "../context/AuthContext";
import { colors, statusLabels, statusColors } from "../theme";

export default function DeliveryListScreen({ navigation }) {
  const { user } = useAuth();
  const [deliveries, setDeliveries] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [isOffline, setIsOffline] = useState(false);
  const [syncingIds, setSyncingIds] = useState(() => getSyncingIds());

  const load = useCallback(async () => {
    setError("");
    try {
      const { fromCache, deliveries: data } = await fetchMyDeliveries();
      setIsOffline(fromCache);
      // Active deliveries first, most-recently-updated within each
      // group — mirrors the priority a dispatcher's own table applies,
      // so an agent's first glance shows what still needs action.
      const activeFirst = [...data].sort((a, b) => {
        const aDone = a.status === "delivered" || a.status === "cancelled";
        const bDone = b.status === "delivered" || b.status === "cancelled";
        if (aDone !== bDone) return aDone ? 1 : -1;
        return new Date(b.updated_at) - new Date(a.updated_at);
      });
      // Overlay what is stored on this device: the agent's own unsent / blocked / conflicted changes must be
      // visible (and a blocked record for a delivery that is no longer theirs must not silently disappear).
      setDeliveries(mergeLocalSyncState(activeFirst, await getCachedDeliveries()));
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  // Reload whenever a sync starts, finishes, or a recovery action is taken, and track what is in flight right now.
  useEffect(() => subscribeSyncState(() => { setSyncingIds(getSyncingIds()); load(); }), [load]);

  const summary = summarizeSyncStates(deliveries, syncingIds);

  function handleRecoveryAction(record, actionId) {
    if (actionId === "retry") {
      retryBlocked(record.id).then(() => runSync()).then(load);
    } else if (actionId === "acknowledge") {
      acknowledgeConflictNotice(record.id).then(load);
    } else if (actionId === "discard") {
      Alert.alert(
        "Discard your change?",
        `Your unsent change to ${record.order_id} will be removed from this phone. The delivery on the server is not affected.`,
        [
          { text: "Keep", style: "cancel" },
          { text: "Discard", style: "destructive", onPress: () => discardBlocked(record.id).then(load) },
        ]
      );
    }
  }

  function renderItem({ item }) {
    const problem = describeSyncProblem(item);
    return (
      <TouchableOpacity style={styles.card} onPress={() => navigation.navigate("DeliveryDetail", { deliveryId: item.id })}>
        <View style={styles.cardHeader}>
          <Text style={styles.orderId}>{item.order_id}</Text>
          <View style={[styles.badge, { backgroundColor: `${statusColors[item.status]}26` }]}>
            <Text style={[styles.badgeText, { color: statusColors[item.status] }]}>
              {statusLabels[item.status] || item.status}
            </Text>
          </View>
        </View>
        {problem ? (
          <Text style={[styles.pendingTag, problem.state === "blocked" && { color: colors.danger }]}>
            {problem.state === "blocked" ? "⛔ Can't sync: " : problem.state === "conflicted" ? "⚠ " : "⏳ "}
            {problem.detail}
          </Text>
        ) : item.sync_status === "pending" ? (
          <Text style={styles.pendingTag}>
            {syncingIds.has(item.id) ? "🔄 Syncing…" : "⏳ Saved offline — will sync automatically"}
          </Text>
        ) : null}
        {item.location_note ? <Text style={styles.meta}>{item.location_note}</Text> : null}
        {item.expected_by ? (
          <Text style={styles.meta}>Expected by {new Date(item.expected_by).toLocaleString()}</Text>
        ) : null}
      </TouchableOpacity>
    );
  }

  return (
    <View style={styles.container}>
      {isOffline && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineBannerText}>
            Working offline — showing your last synced deliveries
            {summary.pending > 0 ? ` (${summary.pending} update${summary.pending === 1 ? "" : "s"} queued to sync)` : ""}
          </Text>
        </View>
      )}
      <SyncStatusPanel records={deliveries} syncingIds={syncingIds} onAction={handleRecoveryAction} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <FlatList
        data={deliveries}
        keyExtractor={(item) => item.id}
        renderItem={renderItem}
        refreshControl={<RefreshControl refreshing={isLoading} onRefresh={load} tintColor={colors.accent} />}
        contentContainerStyle={{ padding: 16 }}
        ListEmptyComponent={
          !isLoading ? (
            <Text style={styles.empty}>
              {user && user.role !== "agent"
                ? `This app only shows an agent's assigned deliveries — a ${user.role} account won't have any. Use the web app for dispatching and admin tools.`
                : "No deliveries assigned right now."}
            </Text>
          ) : null
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bgPage },
  card: { backgroundColor: colors.bgSurface, borderRadius: 10, borderWidth: 1, borderColor: colors.border, padding: 16, marginBottom: 12 },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  orderId: { color: colors.textPrimary, fontSize: 16, fontWeight: "700", fontVariant: ["tabular-nums"] },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  badgeText: { fontSize: 11, fontWeight: "600" },
  meta: { color: colors.textSecondary, fontSize: 12, marginTop: 8 },
  pendingTag: { color: colors.accent, fontSize: 11, fontWeight: "600", marginTop: 8 },
  empty: { color: colors.textMuted, textAlign: "center", marginTop: 60, fontSize: 14 },
  error: { color: colors.danger, textAlign: "center", padding: 12 },
  offlineBanner: { backgroundColor: `${colors.accent}1A`, borderWidth: 1, borderColor: colors.accent, borderStyle: "dashed", borderRadius: 8, margin: 16, marginBottom: 0, padding: 10 },
  offlineBannerText: { color: colors.accent, fontSize: 12, fontWeight: "600", textAlign: "center" },
});

