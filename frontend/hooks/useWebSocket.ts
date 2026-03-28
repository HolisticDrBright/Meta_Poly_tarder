"use client";

import { useEffect, useRef, useCallback } from "react";
import { io, Socket } from "socket.io-client";
import { WS_URL } from "@/lib/utils";
import { useMarketStore } from "@/stores/marketStore";
import { useSignalStore } from "@/stores/signalStore";

export function useWebSocket() {
  const socketRef = useRef<Socket | null>(null);
  const updateMarketPrice = useMarketStore((s) => s.updateMarketPrice);
  const setOrderBook = useMarketStore((s) => s.setOrderBook);
  const addSignal = useSignalStore((s) => s.addSignal);
  const addWhaleTrade = useSignalStore((s) => s.addWhaleTrade);
  const addJetEvent = useSignalStore((s) => s.addJetEvent);

  useEffect(() => {
    const socket = io(WS_URL, {
      transports: ["websocket"],
      reconnection: true,
      reconnectionDelay: 2000,
      reconnectionAttempts: 10,
    });

    socket.on("connect", () => {
      console.log("[WS] Connected to backend");
    });

    socket.on("price_update", (data) => {
      updateMarketPrice(data.market_id, data.yes_price);
    });

    socket.on("book_update", (data) => {
      setOrderBook(data);
    });

    socket.on("signal", (data) => {
      addSignal(data);
    });

    socket.on("whale_trade", (data) => {
      addWhaleTrade(data);
    });

    socket.on("jet_event", (data) => {
      addJetEvent(data);
    });

    socket.on("disconnect", () => {
      console.log("[WS] Disconnected");
    });

    socketRef.current = socket;

    return () => {
      socket.disconnect();
    };
  }, [updateMarketPrice, setOrderBook, addSignal, addWhaleTrade, addJetEvent]);

  const subscribe = useCallback((marketId: string) => {
    socketRef.current?.emit("subscribe", { market: marketId });
  }, []);

  const unsubscribe = useCallback((marketId: string) => {
    socketRef.current?.emit("unsubscribe", { market: marketId });
  }, []);

  return { subscribe, unsubscribe, socket: socketRef.current };
}
