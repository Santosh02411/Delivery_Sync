import React from "react";
import { View, Text, TouchableOpacity, StyleSheet } from "react-native";
import { describeSyncProblem, summarizeSyncStates } from "../services/syncOutcome";
import { colors } from "../theme";

const COUNTERS = [
  { key: "pending", label: "Pending", color: colors.accent },
  { key: "syncing", label: "Syncing", color: colors.statusPickedUp },
  { key: "synced", label: "Synced", color: colors.success },
  { key: "conflicted", label: "Conflicted", color: colors.accent },
  { key: "blocked", label: "Blocked", color: colors.danger },
];

/**
 * Pending / syncing / synced / conflicted / blocked counts for everything on this device and, for each
 * record that is not simply waiting or done, WHY — with the recovery actions (try again / discard / got it).
 */
export default function SyncStatusPanel({ records, syncingIds, onAction }) {
  const summary = summarizeSyncStates(records, syncingIds);
  const problems = records.map((record) => ({ record, problem: describeSyncProblem(record) })).filter((entry) => entry.problem);
  if (summary.total === 0) return null;

  return (
    <View style={styles.panel} accessibilityLabel="Sync status">
      <Text style={styles.title}>SYNC STATUS</Text>
      <View style={styles.counters}>
        {COUNTERS.map(({ key, label, color }) => (
          <View key={key} style={[styles.counter, summary[key] > 0 && { backgroundColor: `${color}26` }]}>
            <Text style={[styles.counterNumber, summary[key] > 0 && { color }]}>{summary[key]}</Text>
            <Text style={[styles.counterLabel, summary[key] > 0 && { color }]}>{label}</Text>
          </View>
        ))}
      </View>

      {problems.map(({ record, problem }) => (
        <View key={record.id} style={[styles.problem, { borderColor: problem.tone === "danger" ? colors.danger : colors.accent }]}>
          <Text style={styles.problemTitle}>
            {record.order_id} — {problem.title}
          </Text>
          <Text style={styles.problemDetail}>{problem.detail}</Text>
          {problem.hint ? <Text style={styles.problemHint}>{problem.hint}</Text> : null}
          {problem.actions.length > 0 && (
            <View style={styles.actions}>
              {problem.actions.map((action) => (
                <TouchableOpacity key={action.id} style={styles.actionButton} onPress={() => onAction(record, action.id)} accessibilityLabel={`${action.label} for ${record.order_id}`}>
                  <Text style={styles.actionText}>{action.label}</Text>
                </TouchableOpacity>
              ))}
            </View>
          )}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  panel: { backgroundColor: colors.bgSurface, borderRadius: 10, borderWidth: 1, borderColor: colors.border, padding: 12, margin: 16, marginBottom: 0 },
  title: { color: colors.textSecondary, fontSize: 11, fontWeight: "700", letterSpacing: 0.6, marginBottom: 8 },
  counters: { flexDirection: "row", gap: 6 },
  counter: { flex: 1, alignItems: "center", paddingVertical: 8, borderRadius: 8, backgroundColor: colors.bgSurfaceElevated },
  counterNumber: { color: colors.textSecondary, fontSize: 17, fontWeight: "700" },
  counterLabel: { color: colors.textSecondary, fontSize: 10 },
  problem: { borderWidth: 1, borderRadius: 8, padding: 10, marginTop: 10 },
  problemTitle: { color: colors.textPrimary, fontSize: 13, fontWeight: "700" },
  problemDetail: { color: colors.textPrimary, fontSize: 12, marginTop: 4 },
  problemHint: { color: colors.textSecondary, fontSize: 11, marginTop: 4 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 },
  actionButton: { borderWidth: 1, borderColor: colors.borderLight, borderRadius: 6, paddingHorizontal: 12, paddingVertical: 6 },
  actionText: { color: colors.accent, fontSize: 12, fontWeight: "600" },
});
