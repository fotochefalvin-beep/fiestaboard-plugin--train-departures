"""The bundled iRail station snapshot: lookup, suggestions and search.

Everything here is offline. ``stations.json`` is a snapshot of iRail
``/stations`` in four languages (``scripts/refresh_stations.py``); the plugin
never fetches the station list at runtime, so ``validate_config`` and
``get_options`` make no network call.
"""

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SNAPSHOT_PATH = Path(__file__).resolve().parent / "stations.json"


@dataclass(frozen=True)
class Station:
    id: str
    standardname: str
    names: Tuple[str, ...]

    @property
    def first_name(self) -> str:
        """First half of a bilingual standard name (``Brussel-Centraal``)."""
        return self.standardname.split("/", 1)[0].strip()

    @property
    def is_hub(self) -> bool:
        """Principal station of its NMBS code block (``008892007``).

        Belgian UIC codes ending in ``00`` plus a check digit mark the main
        station of a place (Gent-Sint-Pieters, Brussel-Zuid, Leuven, ...);
        used only to break ties between equally close suggestions.
        """
        digits = self.id.rsplit(".", 1)[-1]
        return digits.isdigit() and int(digits) % 1000 < 10


def normalize(text: str) -> str:
    """Lower-case ASCII words: ``Bruxelles-Central`` -> ``bruxelles central``."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _keys(station: Station) -> List[str]:
    keys = {normalize(station.id), normalize(station.id.rsplit(".", 1)[-1])}
    for name in station.names + (station.standardname,):
        keys.add(normalize(name))
        for half in name.split("/"):
            keys.add(normalize(half))
    keys.discard("")
    return sorted(keys)


class StationIndex:
    """All stations of a snapshot, findable by any of their names or their id."""

    def __init__(self, records: List[dict]):
        self.stations: List[Station] = []
        self._by_key: Dict[str, Station] = {}
        self._keys: Dict[str, List[str]] = {}
        for record in records:
            names = tuple(record.get("names") or ())
            station = Station(record["id"], record["standardname"], names)
            self.stations.append(station)
            self._keys[station.id] = _keys(station)
            for key in self._keys[station.id]:
                self._by_key.setdefault(key, station)
        self.stations.sort(key=lambda s: (normalize(s.standardname), s.id))

    def resolve(self, value: str) -> Optional[Station]:
        """The station a typed name or id means, or None.

        Case-, accent- and punctuation-insensitive; every language's name and
        each ``/`` half of a bilingual standard name count.
        """
        return self._by_key.get(normalize(value))

    def suggest(self, value: str) -> Optional[Station]:
        """The closest station to a name the snapshot does not know."""
        query = normalize(value)
        if not query:
            return None
        best = None
        for station in self.stations:
            for key in self._keys[station.id]:
                whole = difflib.SequenceMatcher(None, query, key).ratio()
                # Compare with the start of the name too, so "Gnt" is close to
                # "Gent-Sint-Pieters" and not only to short names like "Genk".
                start = difflib.SequenceMatcher(None, query, key[: len(query) + 1]).ratio()
                score = (round(max(whole, start), 6), station.is_hub, whole)
                if best is None or score > best[0]:
                    best = (score, station)
        return best[1] if best else None

    def search(self, query: str) -> List[Station]:
        """Stations whose names contain *query*; names that start with it first."""
        needle = normalize(query)
        if not needle:
            return list(self.stations)
        starts, contains = [], []
        for station in self.stations:
            keys = self._keys[station.id]
            if any(f" {needle}" in f" {key}" for key in keys):
                starts.append(station)
            elif any(needle in key for key in keys):
                contains.append(station)
        return starts + contains


@lru_cache(maxsize=1)
def load_index(path: str = str(SNAPSHOT_PATH)) -> StationIndex:
    with open(path, encoding="utf-8") as f:
        return StationIndex(json.load(f))
