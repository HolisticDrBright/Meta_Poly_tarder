"""
FAA Releasable Aircraft Database loader.

Maps tail numbers (N-numbers) to ICAO24 hex codes and owner information.
The FAA releasable database is public and updated regularly.

Download: https://registry.faa.gov/database/ReleasableAircraft.zip
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AircraftRecord:
    n_number: str
    icao24: str
    owner_name: str
    manufacturer: str
    model: str
    year_built: str
    registrant_type: str  # individual, corporation, etc.


class FAARegistry:
    """Load and query the FAA aircraft registry."""

    def __init__(self) -> None:
        self._by_n_number: dict[str, AircraftRecord] = {}
        self._by_icao24: dict[str, AircraftRecord] = {}

    def load_csv(self, master_path: Path) -> int:
        """
        Load the MASTER.txt file from the FAA releasable database.

        Returns the number of records loaded.
        """
        if not master_path.exists():
            logger.warning(f"FAA registry file not found: {master_path}")
            return 0

        count = 0
        try:
            with open(master_path, encoding="latin-1") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    n_num = row.get("N-NUMBER", "").strip()
                    if not n_num:
                        continue
                    record = AircraftRecord(
                        n_number=f"N{n_num}",
                        icao24=row.get("MODE S CODE HEX", "").strip().lower(),
                        owner_name=row.get("NAME", "").strip(),
                        manufacturer=row.get("MFR MDL CODE", "").strip(),
                        model=row.get("MODEL", "").strip(),
                        year_built=row.get("YEAR MFR", "").strip(),
                        registrant_type=row.get("TYPE REGISTRANT", "").strip(),
                    )
                    self._by_n_number[record.n_number] = record
                    if record.icao24:
                        self._by_icao24[record.icao24] = record
                    count += 1
        except Exception as e:
            logger.error(f"Failed to load FAA registry: {e}")

        logger.info(f"FAA registry loaded: {count} aircraft records")
        return count

    def lookup_n_number(self, n_number: str) -> Optional[AircraftRecord]:
        return self._by_n_number.get(n_number.upper())

    def lookup_icao24(self, icao24: str) -> Optional[AircraftRecord]:
        return self._by_icao24.get(icao24.lower())

    def search_owner(self, name: str) -> list[AircraftRecord]:
        """Search by owner name (case-insensitive partial match)."""
        name_lower = name.lower()
        return [
            r for r in self._by_n_number.values()
            if name_lower in r.owner_name.lower()
        ]
