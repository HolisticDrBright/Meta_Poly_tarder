"use client";

import { useEffect, useRef, useCallback } from "react";
import { useMarketStore } from "@/stores/marketStore";
import { useSignalStore } from "@/stores/signalStore";

/**
 * WebSocket hook — connects to the backend live stream.
 * Only runs client-side. Gracefully handles connection failures.
 */
export function useWebSocket() {
  const socketRef = useRef<WebSocket | null>(null);
  const updateMarketPrice = useMarketStore((s) => s.updateMarketPrice);
  const setOrderBook = useMarketStore((s) => s.setOrderBook);
  const addSignal = useSignalStore((s) => s.addSignal);
  const addWhaleTrade = useSignalStore((s) => s.addWhaleTrade);
  const addJetEvent = useSignalStore((s) => s.addJetEvent);

  useEffect(() => {
    // Only run client-side
    if (typeof window === "undefined") return;

    const wsUrl =
      process.env.NEXT_PUBLIC_WS_URL ||
      `ws://${window.location.hostname}:8000/ws/live`;

    let ws: WebSocket;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let reconnectDelay = 1000;
    let alive = true;

    function connect() {
      if (!alive) return;

      try {
        ws = new WebSocket(wsUrl);
        socketRef.current = ws;

        ws.onopen = () => {
          console.log("[WS] Connected to", wsUrl);
          reconnectDelay = 1000; // reset backoff
        };

        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            const { type, data } = msg;

            if (type === "price_update" && data) {
              updateMarketPrice(data.market_id, data.yes_price);
            } else if (type === "book_update" && data) {
              setOrderBook(data);
            } else if (type === "signal" && data) {
              addSignal(data);
            } else if (type === "whale_trade" && data) {
              addWhaleTrade(data);
            } else if (type === "jet_event" && data) {
              addJetEvent(data);
            }
          } catch {
            // Ignore malformed messages
          }
        };

        ws.onclose = () => {
          console.log("[WS] Disconnected, reconnecting in", reconnectDelay, "ms");
          socketRef.current = null;
          if (alive) {
            reconnectTimer = setTimeout(connect, reconnectDelay);
            reconnectDelay = Math.min(reconnectDelay * 2, 30000);
          }
        };

        ws.onerror = () => {
          // onclose will fire after this, triggering reconnect
        };
      } catch (e) {
        console.warn("[WS] Connection failed:", e);
        if (alive) {
          reconnectTimer = setTimeout(connect, reconnectDelay);
          reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        }
      }
    }

    connect();

    return () => {
      alive = false;
      clearTimeout(reconnectTimer);
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [updateMarketPrice, setOrderBook, addSignal, addWhaleTrade, addJetEvent]);

  const subscribe = useCallback((marketId: string) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "subscribe", market: marketId }));
    }
  }, []);

  const unsubscribe = useCallback((marketId: string) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: "unsubscribe", market: marketId }));
    }
  }, []);

  return { subscribe, unsubscribe };
}
