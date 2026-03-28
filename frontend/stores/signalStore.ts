import { create } from "zustand";

export interface Signal {
  id: string;
  strategy: string;
  market_id: string;
  question: string;
  side: "YES" | "NO";
  price: number;
  size_usdc: number;
  confidence: number;
  reason: string;
  kl_divergence: number;
  kelly_fraction: number;
  confluence_count: number;
  timestamp: string;
}

export interface WhaleTradeEvent {
  wallet: string;
  display_name: string;
  tier: string;
  market_id: string;
  question: string;
  side: "YES" | "NO";
  size_usdc: number;
  price: number;
  timestamp: string;
}

export interface JetEvent {
  target_name: string;
  role: string;
  tail: string;
  from_location: string;
  to_poi: string;
  distance_nm: number;
  signal_strength: string;
  market_tags: string[];
  timestamp: string;
}

export interface VolumeSpike {
  market_id: string;
  question: string;
  volume_spike: number;
  pct_change: number;
  timestamp: string;
}

export interface CopyTradeIntent {
  target_name: string;
  market_id: string;
  question: string;
  side: "YES" | "NO";
  size_usdc: number;
  price: number;
  confluence_count: number;
  status: "pending" | "confirmed" | "rejected";
}

interface SignalState {
  signals: Signal[];
  whaleTrades: WhaleTradeEvent[];
  jetEvents: JetEvent[];
  volumeSpikes: VolumeSpike[];
  copyQueue: CopyTradeIntent[];
  smartMoneyIndex: number;
  addSignal: (signal: Signal) => void;
  setSignals: (signals: Signal[]) => void;
  addWhaleTrade: (trade: WhaleTradeEvent) => void;
  setWhaleTrades: (trades: WhaleTradeEvent[]) => void;
  addJetEvent: (event: JetEvent) => void;
  setJetEvents: (events: JetEvent[]) => void;
  setVolumeSpikes: (spikes: VolumeSpike[]) => void;
  setCopyQueue: (queue: CopyTradeIntent[]) => void;
  setSmartMoneyIndex: (smi: number) => void;
}

export const useSignalStore = create<SignalState>((set) => ({
  signals: [],
  whaleTrades: [],
  jetEvents: [],
  volumeSpikes: [],
  copyQueue: [],
  smartMoneyIndex: 50,

  addSignal: (signal) =>
    set((s) => ({ signals: [signal, ...s.signals].slice(0, 200) })),
  setSignals: (signals) => set({ signals }),

  addWhaleTrade: (trade) =>
    set((s) => ({ whaleTrades: [trade, ...s.whaleTrades].slice(0, 100) })),
  setWhaleTrades: (whaleTrades) => set({ whaleTrades }),

  addJetEvent: (event) =>
    set((s) => ({ jetEvents: [event, ...s.jetEvents].slice(0, 50) })),
  setJetEvents: (jetEvents) => set({ jetEvents }),

  setVolumeSpikes: (volumeSpikes) => set({ volumeSpikes }),
  setCopyQueue: (copyQueue) => set({ copyQueue }),
  setSmartMoneyIndex: (smartMoneyIndex) => set({ smartMoneyIndex }),
}));
