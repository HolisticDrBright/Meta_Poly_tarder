/* Rork color system — ported from expo/constants/colors.ts */

export const Colors = {
  background: '#0D0F12',
  surface: '#141820',
  card: '#161B24',
  cardBorder: 'rgba(255,255,255,0.06)',
  surfaceBorder: 'rgba(255,255,255,0.04)',

  cyan: '#00E5FF',
  cyanDim: 'rgba(0,229,255,0.12)',
  coral: '#FF3B5C',
  coralDim: 'rgba(255,59,92,0.12)',
  amber: '#FFB800',
  amberDim: 'rgba(255,184,0,0.12)',
  green: '#00D68F',
  greenDim: 'rgba(0,214,143,0.12)',
  purple: '#A855F7',
  purpleDim: 'rgba(168,85,247,0.12)',

  textPrimary: '#E8ECF2',
  textSecondary: '#8B95A5',
  textTertiary: '#545E6E',

  tabBar: '#0A0C0F',
  tabBarBorder: 'rgba(255,255,255,0.06)',
  tabInactive: '#545E6E',
} as const;

export type Classification = 'PAPER TRADE' | 'WATCHLIST' | 'NO-TRADE' | 'REVIEW';
export type MarketCategory = 'politics' | 'crypto' | 'sports' | 'policy' | 'tech' | 'economics';

export interface MarketOpportunity {
  id: string;
  title: string;
  category: MarketCategory;
  opportunityScore: number;
  edgeEstimate: number;
  classification: Classification;
  sparkline: { value: number }[];
  currentPrice: number;
  volume24h: string;
  lastUpdated: string;
  aiSummary: string;
  fairProbability: number;
  marketProbability: number;
}

export interface PortfolioStats {
  totalPnL: number;
  activePositions: number;
  brierScore: number;
  calibrationGrade: string;
  wins: number;
  losses: number;
  roi: number;
  todayPnL: number;
}

export interface PortfolioGrowthPoint {
  day: string;
  value: number;
}

export interface ActiveTrade {
  id: string;
  title: string;
  direction: 'YES' | 'NO';
  entryPrice: number;
  currentPrice: number;
  pnl: number;
  size: number;
  enteredAt: string;
}

export interface RegimeInfo {
  label: string;
  confidence: 'high' | 'medium' | 'low';
}
