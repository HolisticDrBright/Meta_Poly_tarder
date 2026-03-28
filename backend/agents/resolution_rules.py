"""
Resolution Rules Agent — parses exact market resolution criteria and
blocks trades where the rules are ambiguous or contain wording traps.

This is the highest-value intelligence upgrade. Most Polymarket losses
come from trading the headline instead of the actual resolution criteria.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

RESOLUTION_PROMPT = """Analyze this Polymarket resolution question with extreme precision:

MARKET: {question}
RESOLUTION SOURCE: {resolution_source}
END DATE: {end_date}
DESCRIPTION: {description}

Answer exactly:
1. What specific event/data point triggers YES resolution?
2. What specific event/data point triggers NO resolution?
3. What oracle or data source determines the outcome?
4. What are exact deadline/timing criteria?
5. List any ambiguous language, wording traps, or edge cases.
6. What scenarios could cause unexpected resolution (N/A, void, disputed)?
7. Does the headline match the actual resolution criteria? If not, what is the discrepancy?

Output ONLY valid JSON:
{{
  "resolution_summary": "plain English, one paragraph",
  "triggers_yes": "exact criteria",
  "triggers_no": "exact criteria",
  "oracle": "data source name",
  "deadline_precision": "exact timing",
  "ambiguity_score": 0.0,
  "wording_traps": [],
  "headline_matches_rules": true,
  "headline_vs_rules_gap": "",
  "rule_hazard_flags": [],
  "tradeable_by_rules": true,
  "block_reason": ""
}}"""

# Common wording traps to check even without AI
WORDING_TRAP_PATTERNS = [
    (r"\bat least\b", "at_least_vs_exactly", "'at least' may differ from 'exactly'"),
    (r"\bmore than\b", "more_than_vs_at_least", "'more than' excludes the threshold value"),
    (r"\bclose(?:s|d)? above\b", "close_vs_trade", "'closes above' requires end-of-day, not intraday"),
    (r"\btrade(?:s|d)? above\b", "trade_vs_close", "'trades above' only needs intraday touch"),
    (r"\bfirst reported\b", "reported_vs_confirmed", "'first reported' may differ from 'officially confirmed'"),
    (r"\bcalendar year\b", "calendar_year_ambiguity", "Check if calendar year matches the specific date"),
    (r"\bofficial(?:ly)?\b", "official_definition", "What counts as 'official'? Which source?"),
    (r"\bET\b|\bEST\b|\bEDT\b", "timezone_specified", "Eastern Time specified — verify UTC conversion"),
    (r"\bUTC\b", "utc_timezone", "UTC timezone — verify market close alignment"),
]


@dataclass
class ResolutionMemo:
    """Result of resolution rules analysis."""

    blocked: bool = False
    block_reason: str = ""
    ambiguity_score: float = 0.0
    resolution_summary: str = ""
    triggers_yes: str = ""
    triggers_no: str = ""
    oracle: str = ""
    wording_traps: list[str] = field(default_factory=list)
    headline_matches_rules: bool = True
    headline_vs_rules_gap: str = ""
    rule_hazard_flags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class ResolutionRulesAgent:
    """
    Parse the EXACT market resolution language and block trades
    where the rules are unclear or contain wording traps.
    """

    def __init__(
        self,
        anthropic_api_key: str = "",
        max_ambiguity: float = 0.6,
    ) -> None:
        self.api_key = anthropic_api_key
        self.max_ambiguity = max_ambiguity

    def _check_wording_traps(self, question: str, description: str = "") -> list[str]:
        """Fast local check for common wording traps (no AI needed)."""
        text = f"{question} {description}".lower()
        traps = []
        for pattern, trap_id, explanation in WORDING_TRAP_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                traps.append(f"{trap_id}: {explanation}")
        return traps

    async def analyze(
        self,
        question: str,
        resolution_source: str = "",
        end_date: str = "",
        description: str = "",
    ) -> ResolutionMemo:
        """Full analysis: local trap check + AI analysis if available."""

        # 1. Fast local trap check (always runs)
        local_traps = self._check_wording_traps(question, description)

        # 2. AI-powered deep analysis (if API key available)
        if self.api_key:
            try:
                memo = await self._ai_analyze(question, resolution_source, end_date, description)
                # Merge local traps
                memo.wording_traps = list(set(memo.wording_traps + local_traps))
                return memo
            except Exception as e:
                logger.warning(f"AI resolution analysis failed: {e}")

        # 3. Fallback: local-only analysis
        ambiguity = min(len(local_traps) * 0.15, 1.0)
        blocked = ambiguity > self.max_ambiguity

        return ResolutionMemo(
            blocked=blocked,
            block_reason="Too many wording traps detected" if blocked else "",
            ambiguity_score=ambiguity,
            resolution_summary=f"Local analysis only. Found {len(local_traps)} potential traps.",
            wording_traps=local_traps,
            headline_matches_rules=len(local_traps) == 0,
        )

    async def _ai_analyze(
        self,
        question: str,
        resolution_source: str,
        end_date: str,
        description: str,
    ) -> ResolutionMemo:
        """Call Claude for deep resolution analysis."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        prompt = RESOLUTION_PROMPT.format(
            question=question,
            resolution_source=resolution_source or "Not specified",
            end_date=end_date or "Not specified",
            description=description or "Not provided",
        )

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        # Parse JSON from response (handle code blocks)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found in response")

        ambiguity = float(data.get("ambiguity_score", 0.5))
        tradeable = data.get("tradeable_by_rules", True)
        blocked = ambiguity > self.max_ambiguity or not tradeable

        return ResolutionMemo(
            blocked=blocked,
            block_reason=data.get("block_reason", "") if blocked else "",
            ambiguity_score=ambiguity,
            resolution_summary=data.get("resolution_summary", ""),
            triggers_yes=data.get("triggers_yes", ""),
            triggers_no=data.get("triggers_no", ""),
            oracle=data.get("oracle", ""),
            wording_traps=data.get("wording_traps", []),
            headline_matches_rules=data.get("headline_matches_rules", True),
            headline_vs_rules_gap=data.get("headline_vs_rules_gap", ""),
            rule_hazard_flags=data.get("rule_hazard_flags", []),
            raw=data,
        )
