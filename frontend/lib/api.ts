/**
 * API client — all backend calls go through here.
 * Centralizes error handling and base URL configuration.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    signal: AbortSignal.timeout(15000),
    ...options,
  });
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${err}`);
  }
  return res.json();
}

// ── Markets ────────────────────────────────────────────────────

export function fetchMarkets(limit = 50, minLiquidity = 0) {
  return request<any[]>(
    `/api/markets/?limit=${limit}&min_liquidity=${minLiquidity}`
  );
}

export function fetchMarketDetail(marketId: string) {
  return request<any>(`/api/markets/${marketId}`);
}

export function fetchEntropyScore(
  marketId: string,
  modelP: number,
  bankroll: number
) {
  return request<any>(
    `/api/markets/${marketId}/entropy?model_probability=${modelP}&bankroll=${bankroll}`
  );
}

export function runDebate(
  marketId: string,
  modelProbability = 0.5,
  context = ""
) {
  return request<any>(`/api/markets/${marketId}/debate`, {
    method: "POST",
    body: JSON.stringify({ model_probability: modelProbability, context }),
  });
}

// ── Signals ────────────────────────────────────────────────────

export function fetchSignals(limit = 50) {
  return request<{ signals: any[]; count: number }>(
    `/api/signals/?limit=${limit}`
  );
}

// ── Portfolio ──────────────────────────────────────────────────

export function fetchPositions() {
  return request<{ positions: any[]; total_pnl: number }>(
    "/api/portfolio/positions"
  );
}

export function fetchPortfolioStats() {
  return request<any>("/api/portfolio/stats");
}

export function closePosition(marketId: string) {
  return request<any>("/api/portfolio/close", {
    method: "POST",
    body: JSON.stringify({ market_id: marketId }),
  });
}

export function placeManualOrder(order: {
  market_id: string;
  side: string;
  price: number;
  size_usdc: number;
  reason?: string;
}) {
  return request<any>("/api/portfolio/order", {
    method: "POST",
    body: JSON.stringify(order),
  });
}

// ── Whale / Copy Trade ─────────────────────────────────────────

export function fetchWhaleLeaderboard() {
  return request<{ entries: any[] }>("/api/whale/leaderboard");
}

export function fetchWhaleTrades(limit = 50) {
  return request<{ trades: any[] }>(`/api/whale/trades?limit=${limit}`);
}

export function fetchCopyQueue() {
  return request<{ queue: any[] }>("/api/whale/copy-queue");
}

export function executeCopyTrade(index: number) {
  return request<any>("/api/whale/copy-queue/execute", {
    method: "POST",
    body: JSON.stringify({ index }),
  });
}

export function skipCopyTrade(index: number) {
  return request<any>("/api/whale/copy-queue/skip", {
    method: "POST",
    body: JSON.stringify({ index }),
  });
}

export function setCopyTargetMode(targetName: string, autoCopy: boolean) {
  return request<any>("/api/whale/targets/mode", {
    method: "PUT",
    body: JSON.stringify({ target_name: targetName, auto_copy: autoCopy }),
  });
}

export function fetchSmartMoneyIndex() {
  return request<{ smi: number; bias: string }>("/api/whale/smart-money-index");
}

// ── Jet Tracker ────────────────────────────────────────────────

export function fetchJetFlights() {
  return request<{ flights: any[] }>("/api/jet/active");
}

export function fetchJetSignals() {
  return request<{ signals: any[] }>("/api/jet/signals");
}

// ── System ─────────────────────────────────────────────────────

export function fetchHealth() {
  return request<any>("/health");
}

export function killSwitch() {
  return request<any>("/api/kill", { method: "POST" });
}

export function unkill() {
  return request<any>("/api/unkill", { method: "POST" });
}
