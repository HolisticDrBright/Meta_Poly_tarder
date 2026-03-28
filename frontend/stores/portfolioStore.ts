import { create } from "zustand";

export interface Position {
  id: number;
  market_id: string;
  question: string;
  side: "YES" | "NO";
  entry_price: number;
  size_usdc: number;
  current_price: number;
  strategy: string;
  opened_at: string;
  pnl: number;
  pnl_pct: number;
  hours_to_close: number | null;
}

export interface EquityPoint {
  timestamp: string;
  balance: number;
  unrealized_pnl: number;
  realized_pnl: number;
  strategy?: string;
}

export interface DailyPnl {
  date: string;
  pnl: number;
}

export interface PortfolioStats {
  balance: number;
  total_exposure: number;
  unrealized_pnl: number;
  realized_pnl: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  trades_today: number;
  paper_trading: boolean;
}

export interface MMStatus {
  market_id: string;
  question: string;
  status: "active" | "paused" | "stopped";
  reservation_price: number;
  bid: number;
  ask: number;
  spread_bps: number;
  inventory: number;
  gamma: number;
  pnl: number;
  rewards_today: number;
}

interface PortfolioState {
  positions: Position[];
  equityCurve: EquityPoint[];
  dailyPnl: DailyPnl[];
  stats: PortfolioStats;
  mmStatuses: MMStatus[];
  setPositions: (positions: Position[]) => void;
  setEquityCurve: (curve: EquityPoint[]) => void;
  setDailyPnl: (pnl: DailyPnl[]) => void;
  setStats: (stats: PortfolioStats) => void;
  setMMStatuses: (statuses: MMStatus[]) => void;
}

export const usePortfolioStore = create<PortfolioState>((set) => ({
  positions: [],
  equityCurve: [],
  dailyPnl: [],
  stats: {
    balance: 10000,
    total_exposure: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    win_rate: 0,
    sharpe_ratio: 0,
    max_drawdown: 0,
    trades_today: 0,
    paper_trading: true,
  },
  mmStatuses: [],

  setPositions: (positions) => set({ positions }),
  setEquityCurve: (equityCurve) => set({ equityCurve }),
  setDailyPnl: (dailyPnl) => set({ dailyPnl }),
  setStats: (stats) => set({ stats }),
  setMMStatuses: (mmStatuses) => set({ mmStatuses }),
}));
