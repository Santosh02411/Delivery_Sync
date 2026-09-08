import React, { useCallback, useState } from "react";
import { View, Text, FlatList, TouchableOpacity, StyleSheet, RefreshControl } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { fetchMyDeliveries } from "../services/api";
import { colors, statusLabels, statusColors } from "../theme";

export default function DeliveryListScreen({ navigation }) {
  const [deliveries, setDeliveries] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const data = await fetchMyDeliveries();
      // Active deliveries first, most-recently-updated within each
      // group — mirrors the priority a dispatcher's own table applies,
      // so an agent's first glance shows what still needs action.
      const activeFirst = [...data].sort((a, b) => {
        const aDone = a.status === "delivered" || a.status === "cancelled";
        const bDone = b.status === "delivered" || b.status === "cancelled";
        if (aDone !== bDone) return aDone ? 1 : -1;
        return new Date(b.updated_at) - new Date(a.updated_at);
      });
      setDeliveries(activeFirst);
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

  function renderItem({ item }) {
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
        {item.location_note ? <Text style={styles.meta}>{item.location_note}</Text> : null}
        {item.expected_by ? (
          <Text style={styles.meta}>Expected by {new Date(item.expected_by).toLocaleString()}</Text>
        ) : null}
      </TouchableOpacity>
    );
  }

  return (
    <View style={styles.container}>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <FlatList
        data={deliveries}
        keyExtractor={(item) => item.id}
        renderItem={renderItem}
        refreshControl={<RefreshControl refreshing={isLoading} onRefresh={load} tintColor={colors.accent} />}
        contentContainerStyle={{ padding: 16 }}
        ListEmptyComponent={
          !isLoading ? <Text style={styles.empty}>No deliveries assigned right now.</Text> : null
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
  empty: { color: colors.textMuted, textAlign: "center", marginTop: 60, fontSize: 14 },
  error: { color: colors.danger, textAlign: "center", padding: 12 },
});
