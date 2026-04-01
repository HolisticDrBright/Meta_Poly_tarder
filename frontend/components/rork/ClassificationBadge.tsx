"use client";

import { Colors, type Classification } from "@/lib/rork-types";

function getBadgeStyle(c: Classification) {
  switch (c) {
    case "PAPER TRADE": return { bg: Colors.cyanDim, text: Colors.cyan };
    case "WATCHLIST": return { bg: Colors.amberDim, text: Colors.amber };
    case "NO-TRADE": return { bg: Colors.coralDim, text: Colors.coral };
    case "REVIEW": return { bg: Colors.purpleDim, text: Colors.purple };
  }
}

export default function ClassificationBadge({ classification }: { classification: Classification }) {
  const s = getBadgeStyle(classification);
  return (
    <span
      className="inline-block px-2 py-0.5 rounded font-mono text-[9px] font-bold tracking-wider"
      style={{ backgroundColor: s.bg, color: s.text }}
    >
      {classification}
    </span>
  );
}
