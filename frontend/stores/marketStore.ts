import { create } from "zustand";

export interface Market {
  id: string;
  condition_id?: string;
  question: string;
  category: string;
  yes_price: number;
  no_price: number;
  liquidity: number;
  volume_24h: number;
  end_date: string | null;
  spread: number;
  entropy_bits: number;
  best_bid?: number;
  best_ask?: number;
  arb_edge?: number;
  kl_divergence?: number;
  model_probability?: number;
}

export interface EntropyScore {
  market_id: string;
  question: string;
  market_price: number;
  model_probability: number;
  entropy_bits: number;
  kl_divergence: number;
  kelly_fraction: number;
  quarter_kelly: number;
  entropy_efficiency: number;
  recommended_action: string;
  position_size_usdc: number;
  edge_strength: string;
}

export interface OrderBookLevel {
  price: number;
  size: number;
}

export interface OrderBookData {
  market_id: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  mid_price: number;
  spread: number;
  ofi: number;
  vpin: number;
}

interface MarketState {
  markets: Market[];
  selectedMarketId: string | null;
  selectedMarket: Market | null;
  entropyScores: EntropyScore[];
  orderBook: OrderBookData | null;
  loading: boolean;
  setMarkets: (markets: Market[]) => void;
  selectMarket: (id: string) => void;
  setEntropyScores: (scores: EntropyScore[]) => void;
  setOrderBook: (book: OrderBookData) => void;
  updateMarketPrice: (id: string, yes_price: number) => void;
}

export const useMarketStore = create<MarketState>((set, get) => ({
  markets: [],
  selectedMarketId: null,
  selectedMarket: null,
  entropyScores: [],
  orderBook: null,
  loading: false,

  setMarkets: (markets) => set({ markets }),

  selectMarket: (id) => {
    const market = get().markets.find((m) => m.id === id) || null;
    set({ selectedMarketId: id, selectedMarket: market });
  },

  setEntropyScores: (scores) => set({ entropyScores: scores }),

  setOrderBook: (book) => set({ orderBook: book }),

  updateMarketPrice: (id, yes_price) =>
    set((state) => ({
      markets: state.markets.map((m) =>
        m.id === id ? { ...m, yes_price, no_price: 1 - yes_price } : m
      ),
    })),
}));
