import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Runtime URL detection: works on VPS, localhost, or any host.
// NEXT_PUBLIC_API_URL is baked at build time — if not set, detect from browser.
function getApiUrl(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== "undefined") return `http://${window.location.hostname}:8000`;
  return "http://localhost:8000";
}

function getWsUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
  if (typeof window !== "undefined") return `ws://${window.location.hostname}:8000/ws/live`;
  return "ws://localhost:8000/ws/live";
}

export const API_URL = getApiUrl();
export const WS_URL = getWsUrl();

export async function apiFetch<T>(path: string): Promise<T> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
  } catch (e) {
    // Return empty/default data instead of crashing the page
    console.warn(`API fetch failed for ${path}:`, e);
    throw e;
  }
}

export function formatUSD(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(n);
}

export function formatPct(n: number, decimals = 1): string {
  return `${(n * 100).toFixed(decimals)}%`;
}

export function formatBits(n: number): string {
  return `${n.toFixed(4)} bits`;
}

export function timeAgo(date: Date): string {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function hoursUntil(dateStr: string | null): number | null {
  if (!dateStr) return null;
  const ms = new Date(dateStr).getTime() - Date.now();
  return Math.max(0, ms / 3600000);
}
