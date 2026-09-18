import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, TextInput, TouchableOpacity, StyleSheet, FlatList, KeyboardAvoidingView, Platform, ActivityIndicator } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { fetchDeliveryMessages, sendDeliveryMessage } from "../services/api";
import { useAuth } from "../context/AuthContext";
import { colors } from "../theme";

// Polling-based, not a real websocket subscription — an honest,
// deliberate scope decision, not an oversight. The backend already
// has a real-time chat_room websocket channel (see
// backend/app/services/websocket_manager.py, used by the web app), but
// wiring a websocket client into this app means also handling
// reconnect-on-background/foreground and reconnect-on-network-change
// correctly for a mobile OS's much more aggressive connection
// lifecycle than a browser tab's — a genuinely different, larger piece
// of work than the polling here, which reuses patterns this app
// already has (see ../services/offlineSync.js's own foreground/
// interval-triggered checks). 10s while this screen is focused is a
// reasonable "feels live enough" interval for a delivery chat thread,
// not a battery concern the way a background location tick is.
const POLL_INTERVAL_MS = 10000;

export default function MessagesScreen({ route }) {
  const { deliveryId, orderId } = route.params;
  const { user } = useAuth();
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const listRef = useRef(null);
  const intervalRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchDeliveryMessages(deliveryId);
      setMessages(data);
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, [deliveryId]);

  useFocusEffect(
    useCallback(() => {
      load();
      intervalRef.current = setInterval(load, POLL_INTERVAL_MS);
      return () => clearInterval(intervalRef.current);
    }, [load])
  );

  async function handleSend() {
    const text = draft.trim();
    if (!text) return;
    setIsSending(true);
    setDraft("");
    try {
      const sent = await sendDeliveryMessage(deliveryId, text);
      setMessages((prev) => [...prev, sent]);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    } catch (err) {
      setError(err.message);
      setDraft(text); // give the agent their unsent text back to retry
    } finally {
      setIsSending(false);
    }
  }

  function renderItem({ item }) {
    const isMine = item.sender_id === user?.id;
    return (
      <View style={[styles.bubbleRow, isMine ? styles.bubbleRowMine : styles.bubbleRowTheirs]}>
        <View style={[styles.bubble, isMine ? styles.bubbleMine : styles.bubbleTheirs]}>
          {!isMine && <Text style={styles.senderName}>{item.sender_display_name} · {item.sender_role}</Text>}
          <Text style={isMine ? styles.messageTextMine : styles.messageText}>{item.message}</Text>
          <Text style={styles.timestamp}>{new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</Text>
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView style={styles.container} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={90}>
      <View style={styles.header}>
        <Text style={styles.headerText}>{orderId}</Text>
      </View>

      {isLoading ? (
        <ActivityIndicator color={colors.accent} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          contentContainerStyle={{ padding: 16 }}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
          ListEmptyComponent={<Text style={styles.empty}>No messages yet — say hello.</Text>}
        />
      )}

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={draft}
          onChangeText={setDraft}
          placeholder="Message dispatch…"
          placeholderTextColor={colors.textMuted}
          multiline
        />
        <TouchableOpacity style={styles.sendButton} onPress={handleSend} disabled={isSending || !draft.trim()}>
          <Text style={styles.sendButtonText}>Send</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bgPage },
  header: { padding: 14, borderBottomWidth: 1, borderBottomColor: colors.border, backgroundColor: colors.bgSurface },
  headerText: { color: colors.textSecondary, fontSize: 12, fontFamily: "monospace" },
  empty: { color: colors.textMuted, textAlign: "center", marginTop: 40, fontSize: 13 },
  bubbleRow: { marginBottom: 10, flexDirection: "row" },
  bubbleRowMine: { justifyContent: "flex-end" },
  bubbleRowTheirs: { justifyContent: "flex-start" },
  bubble: { maxWidth: "78%", borderRadius: 12, padding: 10 },
  bubbleMine: { backgroundColor: colors.accent, borderBottomRightRadius: 3 },
  bubbleTheirs: { backgroundColor: colors.bgSurface, borderWidth: 1, borderColor: colors.border, borderBottomLeftRadius: 3 },
  senderName: { color: colors.textMuted, fontSize: 10.5, marginBottom: 3, fontWeight: "600" },
  messageText: { color: colors.textPrimary, fontSize: 14 },
  messageTextMine: { color: colors.accentTextOn, fontSize: 14 },
  timestamp: { color: colors.textMuted, fontSize: 10, marginTop: 4, alignSelf: "flex-end" },
  error: { color: colors.danger, fontSize: 12, textAlign: "center", paddingBottom: 6 },
  inputRow: { flexDirection: "row", alignItems: "flex-end", padding: 12, gap: 8, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.bgSurface },
  input: { flex: 1, backgroundColor: colors.bgInput, borderWidth: 1, borderColor: colors.border, borderRadius: 18, paddingHorizontal: 14, paddingVertical: 10, color: colors.textPrimary, fontSize: 14, maxHeight: 100 },
  sendButton: { backgroundColor: colors.accent, borderRadius: 18, paddingHorizontal: 18, paddingVertical: 10 },
  sendButtonText: { color: colors.accentTextOn, fontWeight: "700", fontSize: 13 },
});
