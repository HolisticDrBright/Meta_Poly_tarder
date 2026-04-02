import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// URL detection: uses same-origin (relative) when behind nginx proxy,
// or explicit URL when NEXT_PUBLIC_API_URL is set to a full URL.
// Empty string = relative = works behind nginx on any port.
function getApiUrl(): string {
  const env = process.env.NEXT_PUBLIC_API_URL;
  // Explicit full URL set (e.g. "http://localhost:8000")
  if (env && env.startsWith("http")) return env;
  // Empty or not set = use relative URLs (same origin, nginx proxies /api)
  return "";
}

function getWsUrl(): string {
  const env = process.env.NEXT_PUBLIC_WS_URL;
  if (env && env.startsWith("ws")) return env;
  // Build WebSocket URL from current page origin
  if (typeof window !== "undefined") {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}/ws/live`;
  }
  return "ws://localhost:8000/ws/live";
}

export const API_URL = getApiUrl();
export const WS_URL = getWsUrl();

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      signal: AbortSignal.timeout(10000),
      ...options,
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
