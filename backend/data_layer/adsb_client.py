"""
ADS-B flight tracking client for jet signal intelligence.

Supports two data sources:
  1. OpenSky Network (free, rate-limited) — primary
  2. ADS-B Exchange via RapidAPI (paid, faster) — fallback/premium

Tracks tail numbers from targets.yaml and cross-references
positions against Points of Interest (POIs) from pois.yaml.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

OPENSKY_BASE = "https://opensky-network.org/api"
ADSBX_BASE = "https://adsbexchange-com1.p.rapidapi.com/v2"


@dataclass
class AircraftPosition:
    """Live position of a tracked aircraft."""

    icao24: str
    callsign: str
    latitude: float
    longitude: float
    altitude_ft: float
    velocity_kts: float
    heading: float
    on_ground: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # enriched fields (filled after target matching)
    tail_number: str = ""
    target_name: str = ""
    target_role: str = ""


@dataclass
class PointOfInterest:
    """A location relevant to a prediction market."""

    name: str
    latitude: float
    longitude: float
    category: str  # "pharma_hq", "fda", "congress", "white_house", etc.
    market_tags: list[str] = field(default_factory=list)


@dataclass
class JetSignal:
    """A signal generated when a tracked jet approaches a POI."""

    aircraft: AircraftPosition
    poi: PointOfInterest
    distance_nm: float
    signal_strength: str  # "strong" | "moderate" | "weak"
    market_tags: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_actionable(self) -> bool:
        return self.signal_strength in ("strong", "moderate")


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in nautical miles."""
    R_NM = 3440.065  # Earth radius in nautical miles
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R_NM * math.asin(math.sqrt(a))


class ADSBClient:
    """Flight tracking client supporting OpenSky and ADS-B Exchange."""

    def __init__(
        self,
        opensky_user: str = "",
        opensky_pass: str = "",
        adsbx_api_key: str = "",
    ) -> None:
        self.opensky_user = opensky_user
        self.opensky_pass = opensky_pass
        self.adsbx_api_key = adsbx_api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_aircraft_opensky(self, icao24_list: list[str]) -> list[AircraftPosition]:
        """Fetch positions from OpenSky Network."""
        session = await self._get_session()
        auth = None
        if self.opensky_user and self.opensky_pass:
            auth = aiohttp.BasicAuth(self.opensky_user, self.opensky_pass)

        params = {"icao24": ",".join(icao24_list)}
        try:
            async with session.get(
                f"{OPENSKY_BASE}/states/all",
                params=params,
                auth=auth,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return self._parse_opensky(data)
        except Exception as e:
            logger.error(f"OpenSky fetch failed: {e}")
            return []

    def _parse_opensky(self, data: dict[str, Any]) -> list[AircraftPosition]:
        """Parse OpenSky state vector response."""
        states = data.get("states", [])
        positions = []
        for s in states:
            if s[6] is None or s[5] is None:  # lat/lon missing
                continue
            positions.append(
                AircraftPosition(
                    icao24=s[0],
                    callsign=(s[1] or "").strip(),
                    latitude=float(s[6]),
                    longitude=float(s[5]),
                    altitude_ft=float(s[7] or 0) * 3.28084,  # meters to feet
                    velocity_kts=float(s[9] or 0) * 1.94384,  # m/s to knots
                    heading=float(s[10] or 0),
                    on_ground=bool(s[8]),
                )
            )
        return positions

    async def get_aircraft_adsbx(self, icao24: str) -> list[AircraftPosition]:
        """Fetch position from ADS-B Exchange (RapidAPI)."""
        if not self.adsbx_api_key:
            return []
        session = await self._get_session()
        headers = {
            "X-RapidAPI-Key": self.adsbx_api_key,
            "X-RapidAPI-Host": "adsbexchange-com1.p.rapidapi.com",
        }
        try:
            async with session.get(
                f"{ADSBX_BASE}/icao/{icao24}/",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                ac_list = data.get("ac", [])
                return [
                    AircraftPosition(
                        icao24=ac.get("hex", icao24),
                        callsign=ac.get("flight", "").strip(),
                        latitude=float(ac.get("lat", 0)),
                        longitude=float(ac.get("lon", 0)),
                        altitude_ft=float(ac.get("alt_baro", 0)),
                        velocity_kts=float(ac.get("gs", 0)),
                        heading=float(ac.get("track", 0)),
                        on_ground=ac.get("alt_baro", "ground") == "ground",
                    )
                    for ac in ac_list
                    if ac.get("lat") is not None
                ]
        except Exception as e:
            logger.error(f"ADS-B Exchange fetch failed: {e}")
            return []

    def check_proximity(
        self,
        positions: list[AircraftPosition],
        pois: list[PointOfInterest],
        max_distance_nm: float = 50.0,
    ) -> list[JetSignal]:
        """Check if any tracked aircraft are near any POIs."""
        signals = []
        for pos in positions:
            for poi in pois:
                dist = haversine_nm(pos.latitude, pos.longitude, poi.latitude, poi.longitude)
                if dist <= max_distance_nm:
                    if dist < 10:
                        strength = "strong"
                    elif dist < 30:
                        strength = "moderate"
                    else:
                        strength = "weak"
                    signals.append(
                        JetSignal(
                            aircraft=pos,
                            poi=poi,
                            distance_nm=dist,
                            signal_strength=strength,
                            market_tags=poi.market_tags,
                        )
                    )
        return signals

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
