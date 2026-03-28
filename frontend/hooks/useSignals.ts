"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/utils";
import { useMarketStore, type Market } from "@/stores/marketStore";
import { useSignalStore } from "@/stores/signalStore";
import { usePortfolioStore } from "@/stores/portfolioStore";

export function useMarkets(limit = 50, minLiquidity = 0) {
  const setMarkets = useMarketStore((s) => s.setMarkets);
  return useQuery({
    queryKey: ["markets", limit, minLiquidity],
    queryFn: async () => {
      const data = await apiFetch<Market[]>(
        `/api/markets?limit=${limit}&min_liquidity=${minLiquidity}`
      );
      setMarkets(data);
      return data;
    },
    refetchInterval: 45_000,
  });
}

export function useMarketDetail(marketId: string | null) {
  return useQuery({
    queryKey: ["market", marketId],
    queryFn: () => apiFetch(`/api/markets/${marketId}`),
    enabled: !!marketId,
    refetchInterval: 10_000,
  });
}

export function useEntropyScore(marketId: string | null, modelP = 0.5, bankroll = 10000) {
  return useQuery({
    queryKey: ["entropy", marketId, modelP, bankroll],
    queryFn: () =>
      apiFetch(
        `/api/markets/${marketId}/entropy?model_probability=${modelP}&bankroll=${bankroll}`
      ),
    enabled: !!marketId,
  });
}

export function useSignals() {
  const setSignals = useSignalStore((s) => s.setSignals);
  return useQuery({
    queryKey: ["signals"],
    queryFn: async () => {
      const data = await apiFetch<{ signals: any[]; count: number }>(
        "/api/signals"
      );
      setSignals(data.signals);
      return data;
    },
    refetchInterval: 30_000,
  });
}

export function usePortfolioStats() {
  const setStats = usePortfolioStore((s) => s.setStats);
  return useQuery({
    queryKey: ["portfolio-stats"],
    queryFn: async () => {
      const data = await apiFetch<any>("/api/portfolio/stats");
      setStats(data);
      return data;
    },
    refetchInterval: 30_000,
  });
}

export function usePositions() {
  const setPositions = usePortfolioStore((s) => s.setPositions);
  return useQuery({
    queryKey: ["positions"],
    queryFn: async () => {
      const data = await apiFetch<{ positions: any[] }>("/api/portfolio/positions");
      setPositions(data.positions);
      return data.positions;
    },
    refetchInterval: 15_000,
  });
}

export function useWhaleLeaderboard() {
  return useQuery({
    queryKey: ["leaderboard"],
    queryFn: () => apiFetch("/api/whale/leaderboard"),
    refetchInterval: 60_000,
  });
}

export function useJetFlights() {
  const setJetEvents = useSignalStore((s) => s.setJetEvents);
  return useQuery({
    queryKey: ["jet-active"],
    queryFn: async () => {
      const data = await apiFetch<{ flights: any[] }>("/api/jet/active");
      setJetEvents(data.flights);
      return data;
    },
    refetchInterval: 60_000,
  });
}
