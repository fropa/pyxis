import { useEffect, useRef } from "react";
import { useAppStore, WsEvent } from "../store";

// Derive WS URL from current page location so it works on any host/port.
// Falls back to explicit VITE_WS_URL when running the Vite dev server directly.
const WS_URL = import.meta.env.VITE_WS_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`
    : "ws://localhost:8000");

export function useWebSocket() {
  const apiKey = useAppStore((s) => s.apiKey);
  const pushEvent = useAppStore((s) => s.pushEvent);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!apiKey) return;

    const connect = () => {
      const ws = new WebSocket(`${WS_URL}/ws/events?api_key=${apiKey}`);
      wsRef.current = ws;

      ws.onmessage = (e) => {
        try {
          const event: WsEvent = JSON.parse(e.data);
          pushEvent(event);
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        // Reconnect after 3s
        setTimeout(connect, 3000);
      };
    };

    connect();

    return () => {
      wsRef.current?.close();
    };
  }, [apiKey, pushEvent]);
}
