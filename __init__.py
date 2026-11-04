"""Train Departures: the next direct NMBS/SNCB train on one route.

Every refresh reads iRail ``/connections`` for the configured from/to
stations and shows the next direct train that has not left yet, with its
delay and platform, plus the train after it. Stations are checked and
searched offline against a bundled iRail ``/stations`` snapshot
(``stations.json``), so saving settings never touches the network.

Variables (all upper-case ASCII, at most 15 characters, for a 3 x 15 Note):
``status``, ``route``, ``from_label``, ``to_label``, ``time``, ``delay``,
``platform``, ``next_time``, ``next_delay``.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.plugins.base import Option, OptionsRequest, OptionsResult, PluginBase, PluginResult

try:
    from . import irail, stations
except ImportError:  # pragma: no cover - imported outside its package, e.g. by
    # pytest collecting this repo's root as a top-level module.
    import irail  # type: ignore[no-redef]
    import stations  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

PLUGIN_ID = "train_departures"
MAX_LEN = 15
FALLBACK_TZ = "Europe/Brussels"
#: Catalogs of the station pickers. Core rejects one ``options_id`` on two
#: fields, so the to-station picker asks for the same list under its own id.
STATIONS_OPTIONS_IDS = ("stations", "to_stations")

STATUS_OK = "OK"
STATUS_NO_DATA = "NO TRAIN DATA"
STATUS_NO_DIRECT = "NO DIRECT TRAIN"

TRAIN_FIELDS = ("time", "delay", "platform", "next_time", "next_delay")
STATION_FIELDS = (("from_station", "From station"), ("to_station", "To station"))


def board_text(value: Any, limit: int = MAX_LEN) -> str:
    """Upper-case ASCII for the board, cut to *limit*: ``Liège`` -> ``LIEGE``."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text[:limit].rstrip()


def select_trains(connections: List[irail.Connection], now: float) -> List[irail.Connection]:
    """Direct trains still to leave, the one that actually leaves first first.

    Drops connections with vias, cancelled ones and ones that left
    (``left`` flag, or scheduled time + delay already past), dedupes by
    (vehicle, time), then sorts by expected departure, then scheduled time.
    """
    usable: Dict[Tuple[str, int], irail.Connection] = {}
    for conn in connections:
        if not conn.direct or conn.canceled or conn.left:
            continue
        if conn.time + conn.delay < now:
            continue
        usable.setdefault((conn.vehicle, conn.time), conn)
    return sorted(usable.values(), key=lambda c: (c.time + c.delay, c.time))


def format_delay(seconds: int) -> str:
    minutes = seconds // 60
    return f"+{minutes}" if minutes > 0 else ""


def format_platform(platform: str) -> str:
    text = board_text(platform, limit=MAX_LEN - 1)
    if not text or text == "?":
        return ""
    return f"P{text}"


def local_zone() -> ZoneInfo:
    """FiestaBoard's general timezone, else Europe/Brussels."""
    name = ""
    try:
        from src.config import Config

        name = Config.GENERAL_TIMEZONE or ""
    except Exception:  # pragma: no cover - config unreadable: use the fallback
        logger.debug("General timezone unavailable", exc_info=True)
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown general timezone %r, using %s", name, FALLBACK_TZ)
    return ZoneInfo(FALLBACK_TZ)


def unknown_station_message(index: stations.StationIndex, value: str) -> str:
    suggestion = index.suggest(value)
    message = f"unknown station '{value}'"
    if suggestion is not None:
        message += f" — did you mean {board_text(suggestion.standardname, limit=200)}?"
    return message


class Plugin(PluginBase):
    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        #: Epoch seconds now; tests replace it.
        self._now: Callable[[], float] = time.time
        #: Builds the iRail client; tests replace it with a fake session.
        self.client_factory: Callable[[], irail.IRailClient] = irail.IRailClient
        self._client: Optional[irail.IRailClient] = None

    @property
    def plugin_id(self) -> str:
        return PLUGIN_ID

    # settings ----------------------------------------------------------------

    @staticmethod
    def _index() -> stations.StationIndex:
        return stations.load_index()

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Offline: stations against the bundled snapshot, labels' type.

        ``refresh_seconds`` is checked by the host first, against the
        manifest's ``min_refresh_seconds`` (30).
        """
        errors: List[str] = []
        index = self._index()
        resolved = {}
        for key, title in STATION_FIELDS:
            value = config.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{title} is required")
                continue
            station = index.resolve(value)
            if station is None:
                errors.append(unknown_station_message(index, value.strip()))
            else:
                resolved[key] = station
        if len(resolved) == 2 and resolved["from_station"].id == resolved["to_station"].id:
            errors.append("From and to station must be different")
        for key, title in (("from_label", "From label"), ("to_label", "To label")):
            value = config.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"{title} must be text")
        return errors

    def on_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        self.clear_cache()

    def get_options(self, request: OptionsRequest) -> OptionsResult:
        """The station picker: the bundled snapshot, filtered by the typed query."""
        if request.options_id not in STATIONS_OPTIONS_IDS:
            raise NotImplementedError(request.options_id)
        matches = self._index().search(request.query or "")
        limit = max(1, int(request.limit or 200))
        page = matches[:limit]
        options = [
            Option(
                value=station.standardname,
                label=station.standardname,
                description=", ".join(n for n in station.names if n != station.standardname) or None,
            )
            for station in page
        ]
        return OptionsResult(options=options, has_more=len(matches) > limit, total=len(matches))

    # data --------------------------------------------------------------------

    def _label(self, key: str, station: stations.Station) -> str:
        value = self.config.get(key)
        text = board_text(value) if isinstance(value, str) else ""
        return text or board_text(station.first_name)

    def _get_client(self) -> irail.IRailClient:
        if self._client is None:
            self._client = self.client_factory()
        return self._client

    def fetch_data(self) -> PluginResult:
        index = self._index()
        route: Dict[str, stations.Station] = {}
        for key, title in STATION_FIELDS:
            value = self.config.get(key)
            station = index.resolve(value) if isinstance(value, str) else None
            if station is None:
                return PluginResult(available=False, error=f"{title} is not set to a known station")
            route[key] = station
        from_st, to_st = route["from_station"], route["to_station"]

        from_label = self._label("from_label", from_st)
        to_label = self._label("to_label", to_st)
        data: Dict[str, str] = {
            "status": STATUS_OK,
            "route": f"{from_label}>{to_label}"[:MAX_LEN],
            "from_label": from_label,
            "to_label": to_label,
        }
        data.update({field: "" for field in TRAIN_FIELDS})

        try:
            connections = self._get_client().connections(from_st.id, to_st.id)
        except irail.StationNotFound as exc:
            gone = {"from": [from_st], "to": [to_st]}.get(exc.parameter or "", [from_st, to_st])
            names = " or ".join(f"'{s.standardname}'" for s in gone)
            logger.warning("iRail does not know %s: %s", names, exc)
            return PluginResult(
                available=False,
                error=f"iRail no longer knows station {names}; pick it again in the plugin settings",
            )
        except irail.Unavailable as exc:
            logger.warning("No train data: %s", exc)
            data["status"] = STATUS_NO_DATA
            return PluginResult(available=True, data=data)

        trains = select_trains(connections, self._now())
        if not trains:
            data["status"] = STATUS_NO_DIRECT
            return PluginResult(available=True, data=data)

        zone = local_zone()

        def hhmm(conn: irail.Connection) -> str:
            return datetime.fromtimestamp(conn.time, zone).strftime("%H:%M")

        first = trains[0]
        data.update(time=hhmm(first), delay=format_delay(first.delay), platform=format_platform(first.platform))
        if len(trains) > 1:
            data.update(next_time=hhmm(trains[1]), next_delay=format_delay(trains[1].delay))
        return PluginResult(available=True, data=data)
