import React, { useEffect, useState, useRef } from "react";
import { fetchDeliveryMessages, sendDeliveryMessage } from "../services/api";
import { useAuth } from "../context/AuthContext";

const POLL_INTERVAL_MS = 5000;

/**
 * A simple chat thread for one delivery, between the assigned agent and
 * their organization's dispatchers/admins. Separate from the delivery's
 * "notes" field (a single current-state note) and from the status
 * history log (an automatic audit trail) — this is an actual
 * back-and-forth conversation.
 *
 * Polls for new messages every 5s while the modal is open, rather than
 * requiring a manual refresh — a chat thread that doesn't show the
 * other person's reply without being told to check again isn't a very
 * useful chat thread.
 */
export default function DeliveryMessages({ deliveryId, isSyncedToServer }) {
  const { token, user } = useAuth();
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState(null);
  const threadEndRef = useRef(null);

  useEffect(() => {
    if (!isSyncedToServer) return; // no server-side id to attach messages to yet

    loadMessages();
    const intervalId = setInterval(loadMessages, POLL_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, [deliveryId, isSyncedToServer]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  async function loadMessages() {
    try {
      const data = await fetchDeliveryMessages(token, deliveryId);
      setMessages(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    if (!newMessage.trim()) return;

    setIsSending(true);
    try {
      await sendDeliveryMessage(token, deliveryId, newMessage.trim());
      setNewMessage("");
      await loadMessages();
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSending(false);
    }
  }

  if (!isSyncedToServer) {
    return (
      <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
        This delivery hasn't synced to the server yet, so messaging isn't
        available until it does.
      </p>
    );
  }

  return (
    <div>
      <div
        style={{
          maxHeight: "220px",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          marginBottom: "10px",
        }}
      >
        {messages.length === 0 && (
          <p style={{ color: "var(--text-muted)", fontSize: "13px" }}>
            No messages yet. Say something below.
          </p>
        )}
        {messages.map((m) => {
          const isMine = m.sender_id === user.id;
          return (
            <div
              key={m.id}
              style={{
                alignSelf: isMine ? "flex-end" : "flex-start",
                maxWidth: "80%",
                backgroundColor: isMine ? "var(--accent)" : "var(--bg-input)",
                color: isMine ? "var(--accent-text-on)" : "var(--text-primary)",
                padding: "8px 12px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <div style={{ fontSize: "11px", fontWeight: 600, opacity: 0.85, marginBottom: "2px" }}>
                {m.sender_display_name} · {m.sender_role}
              </div>
              <div style={{ fontSize: "13.5px" }}>{m.message}</div>
              <div style={{ fontSize: "10.5px", opacity: 0.7, marginTop: "3px" }}>
                {new Date(m.created_at).toLocaleString()}
              </div>
            </div>
          );
        })}
        <div ref={threadEndRef} />
      </div>

      {error && <p style={{ color: "var(--danger)", fontSize: "12.5px" }}>{error}</p>}

      <form onSubmit={handleSend} style={{ display: "flex", gap: "8px" }}>
        <input
          className="input"
          type="text"
          value={newMessage}
          onChange={(e) => setNewMessage(e.target.value)}
          placeholder="Type a message..."
          style={{ flexGrow: 1 }}
        />
        <button type="submit" className="btn btn-primary" disabled={isSending || !newMessage.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
