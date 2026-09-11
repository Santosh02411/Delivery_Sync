import React, { useCallback, useState } from "react";
import { View, Text, TouchableOpacity, StyleSheet, ScrollView, ActivityIndicator, Alert } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { getDelivery, updateDeliveryStatus } from "../services/api";
import { colors, statusLabels, statusColors } from "../theme";

// Which status a "Mark as..." button moves a delivery to next — a
// simplified linear happy-path (no failed-attempt reason-code picker,
// no partial-delivery flag, no proof-of-delivery capture) compared to
// the web app's AgentDeliveryList.jsx. Those are real, deliberately
// out-of-scope-for-v1 gaps — see mobile/README.md's own "Not yet built"
// section for the honest list, so this isn't quietly passed off as
// full feature parity with the web agent app.
const NEXT_STATUS = {
  pending: "picked_up",
  picked_up: "out_for_delivery",
  out_for_delivery: "delivered",
};

const NEXT_STATUS_LABEL = {
  pending: "Mark Picked Up",
  picked_up: "Mark Out for Delivery",
  out_for_delivery: "Mark Delivered",
};

export default function DeliveryDetailScreen({ route }) {
  const { deliveryId } = route.params;
  const [delivery, setDelivery] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState("");
  const [isOffline, setIsOffline] = useState(false);
  const [queuedNotice, setQueuedNotice] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const { fromCache, delivery: data } = await getDelivery(deliveryId);
      setIsOffline(fromCache);
      setDelivery(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, [deliveryId]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  async function handleAdvanceStatus() {
    const nextStatus = NEXT_STATUS[delivery.status];
    if (!nextStatus) return;
    setIsUpdating(true);
    setQueuedNotice("");
    try {
      const result = await updateDeliveryStatus(delivery, nextStatus);
      setDelivery(result.delivery);
      if (result.queued) {
        setQueuedNotice(
          "No connection right now — saved on this device and will sync automatically once you're back online."
        );
      }
    } catch (err) {
      Alert.alert("Couldn't update delivery", err.message);
    } finally {
      setIsUpdating(false);
    }
  }

  if (isLoading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  if (error || !delivery) {
    return (
      <View style={styles.centered}>
        <Text style={styles.error}>{error || "Delivery not found."}</Text>
      </View>
    );
  }

  const nextStatus = NEXT_STATUS[delivery.status];

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 20 }}>
      {isOffline && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineBannerText}>Working offline — showing the last synced copy of this delivery</Text>
        </View>
      )}
      {delivery.sync_status === "pending" && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineBannerText}>⏳ This update is saved on this device and waiting to sync</Text>
        </View>
      )}
      {queuedNotice ? (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineBannerText}>{queuedNotice}</Text>
        </View>
      ) : null}
      <Text style={styles.orderId}>{delivery.order_id}</Text>
      <View style={[styles.badge, { backgroundColor: `${statusColors[delivery.status]}26`, alignSelf: "flex-start" }]}>
        <Text style={[styles.badgeText, { color: statusColors[delivery.status] }]}>
          {statusLabels[delivery.status] || delivery.status}
        </Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>Customer contact</Text>
        <Text style={styles.value}>{delivery.customer_email || delivery.customer_phone || "—"}</Text>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>Zone</Text>
        <Text style={styles.value}>{delivery.zone || "—"}</Text>
      </View>

      {delivery.expected_by ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Expected by</Text>
          <Text style={styles.value}>{new Date(delivery.expected_by).toLocaleString()}</Text>
        </View>
      ) : null}

      {delivery.notes ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Notes</Text>
          <Text style={styles.value}>{delivery.notes}</Text>
        </View>
      ) : null}

      {nextStatus && (
        <TouchableOpacity style={styles.button} onPress={handleAdvanceStatus} disabled={isUpdating}>
          {isUpdating ? (
            <ActivityIndicator color={colors.accentTextOn} />
          ) : (
            <Text style={styles.buttonText}>{NEXT_STATUS_LABEL[delivery.status]}</Text>
          )}
        </TouchableOpacity>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bgPage },
  centered: { flex: 1, backgroundColor: colors.bgPage, justifyContent: "center", alignItems: "center" },
  orderId: { color: colors.textPrimary, fontSize: 22, fontWeight: "700", marginBottom: 10 },
  badge: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 14, marginBottom: 24 },
  badgeText: { fontSize: 12, fontWeight: "600" },
  section: { marginBottom: 18 },
  sectionLabel: { color: colors.textMuted, fontSize: 11, textTransform: "uppercase", marginBottom: 4 },
  value: { color: colors.textPrimary, fontSize: 15 },
  button: { backgroundColor: colors.accent, borderRadius: 8, padding: 16, alignItems: "center", marginTop: 20 },
  buttonText: { color: colors.accentTextOn, fontWeight: "700", fontSize: 15 },
  error: { color: colors.danger, fontSize: 14, textAlign: "center", padding: 20 },
  offlineBanner: { backgroundColor: `${colors.accent}1A`, borderWidth: 1, borderColor: colors.accent, borderStyle: "dashed", borderRadius: 8, padding: 10, marginBottom: 14 },
  offlineBannerText: { color: colors.accent, fontSize: 12, fontWeight: "600", textAlign: "center" },
});
