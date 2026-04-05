import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface WsEvent {
  type: string;
  [key: string]: unknown;
}

interface AppState {
  apiKey: string;
  setApiKey: (key: string) => void;

  // Live events from WebSocket
  recentEvents: WsEvent[];
  pushEvent: (event: WsEvent) => void;

  // Active incident shown in sidebar
  activeIncidentId: string | null;
  setActiveIncidentId: (id: string | null) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      apiKey: "",
      setApiKey: (apiKey) => set({ apiKey }),

      recentEvents: [],
      pushEvent: (event) =>
        set((state) => ({
          recentEvents: [event, ...state.recentEvents].slice(0, 200),
        })),

      activeIncidentId: null,
      setActiveIncidentId: (id) => set({ activeIncidentId: id }),
    }),
    {
      name: "infrawatch-store",
      partialize: (state) => ({ apiKey: state.apiKey }),
    }
  )
);
