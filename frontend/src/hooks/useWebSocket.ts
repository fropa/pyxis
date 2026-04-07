import { useEffect, useRef } from "react";
import { useAppStore, WsEvent } from "../store";

const WS_URL = import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;

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
