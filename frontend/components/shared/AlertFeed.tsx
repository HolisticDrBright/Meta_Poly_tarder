"use client";

import { cn, timeAgo } from "@/lib/utils";
import { StrategyBadge, SideBadge } from "./SignalBadge";

interface AlertItem {
  id: string;
  strategy: string;
  message: string;
  side?: "YES" | "NO";
  timestamp: string;
  severity: "info" | "warning" | "critical";
}

const severityColors = {
  info: "border-l-poly-blue",
  warning: "border-l-poly-amber",
  critical: "border-l-poly-red",
};

export function AlertFeed({ alerts }: { alerts: AlertItem[] }) {
  if (!alerts.length) {
    return (
      <div className="text-center text-muted-foreground text-sm py-4">
        No alerts
      </div>
    );
  }

  return (
    <div className="space-y-1 max-h-64 overflow-y-auto">
      {alerts.map((alert) => (
        <div
          key={alert.id}
          className={cn(
            "flex items-start gap-2 p-2 rounded bg-muted/30 border-l-2",
            severityColors[alert.severity]
          )}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <StrategyBadge strategy={alert.strategy} />
              {alert.side && <SideBadge side={alert.side} />}
              <span className="text-xs text-muted-foreground ml-auto">
                {timeAgo(new Date(alert.timestamp))}
              </span>
            </div>
            <p className="text-xs text-foreground/80 truncate">
              {alert.message}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

export function PanelHeader({
  title,
  subtitle,
  refreshInterval,
  status,
}: {
  title: string;
  subtitle?: string;
  refreshInterval?: number;
  status?: "live" | "paused" | "error";
}) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div>
        <h3 className="text-sm font-bold text-foreground">{title}</h3>
        {subtitle && (
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        )}
      </div>
      <div className="flex items-center gap-2">
        {refreshInterval && (
          <span className="text-[10px] text-muted-foreground">
            {refreshInterval}s
          </span>
        )}
        {status && (
          <span
            className={cn(
              "w-2 h-2 rounded-full",
              status === "live" && "bg-poly-green animate-pulse-glow",
              status === "paused" && "bg-poly-amber",
              status === "error" && "bg-poly-red"
            )}
          />
        )}
      </div>
    </div>
  );
}

export function PanelCard({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "h-full bg-card rounded-lg border border-border p-3 overflow-hidden",
        className
      )}
    >
      {children}
    </div>
  );
}
