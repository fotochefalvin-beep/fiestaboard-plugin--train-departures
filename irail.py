"""A small client for iRail ``/connections`` (api.irail.be, free, no key).

Returns parsed connections, or raises one of two typed failures:

* :class:`StationNotFound` -- iRail answered 400 ``RequestedStopNotFoundException``:
  a station from the bundled snapshot is no longer known. A settings problem.
* :class:`Unavailable` -- timeout, connection error, HTTP error, bad JSON:
  iRail is down or confused right now. Try again on the next refresh.

A 404 ``JourneyPlanNotFoundException`` (iRail is up, the planner found no
journey) is an empty list, not a failure.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

CONNECTIONS_URL = "https://api.irail.be/connections/"
USER_AGENT = (
    "fiestaboard-plugin--train-departures "
    "(+https://github.com/fotochefalvin-beep/fiestaboard-plugin--train-departures)"
)
#: (connect, read) seconds. Every request carries it.
TIMEOUT = (5, 10)
RESULTS = 6
#: iRail ``lang`` values; station names (e.g. a train's destination) come back in it.
LANGUAGES = ("en", "nl", "fr", "de")
DEFAULT_LANGUAGE = "nl"


class IRailError(Exception):
    """Base for the two ways a /connections call can fail."""


class Unavailable(IRailError):
    """iRail could not answer usefully right now."""


class StationNotFound(IRailError):
    """iRail does not know one of the requested stations."""

    def __init__(self, message: str, parameter: Optional[str] = None):
        super().__init__(message)
        #: ``"from"``, ``"to"`` or None when iRail did not say which.
        self.parameter = parameter


@dataclass(frozen=True)
class Connection:
    """The departure side of one iRail connection."""

    time: int  # scheduled departure, epoch seconds
    delay: int  # seconds
    platform: str
    canceled: bool
    left: bool
    vehicle: str
    direct: bool
    destination: str = ""  # the train's final stop (``direction.name``), in the request language
    train_type: str = ""  # category: IC, P, L, S1, EC ... (``vehicleinfo.type``)


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true")


def _train_type(departure: dict) -> str:
    """``vehicleinfo.type`` (``S1``), else the leading letters of the vehicle id.

    The id fallback cannot tell ``S1 1790`` from ``S11 790`` (both are
    ``BE.NMBS.S11790``), so an S-train without ``vehicleinfo`` shows ``S``.
    """
    info = departure.get("vehicleinfo")
    if isinstance(info, dict):
        kind = str(info.get("type") or "").strip()
        if kind:
            return kind
    vehicle = str(departure.get("vehicle") or "").rsplit(".", 1)[-1]
    match = re.match(r"[A-Za-z]+", vehicle)
    return match.group(0) if match else ""


def _destination(departure: dict) -> str:
    direction = departure.get("direction")
    if not isinstance(direction, dict):
        return ""
    return str(direction.get("name") or "")


def parse_connection(item: dict) -> Connection:
    """One ``connection[]`` record -> :class:`Connection` (KeyError/ValueError if malformed)."""
    departure = item["departure"]
    return Connection(
        time=int(departure["time"]),
        delay=int(departure.get("delay") or 0),
        platform=str(departure.get("platform") or ""),
        canceled=_flag(departure.get("canceled")),
        left=_flag(departure.get("left")),
        vehicle=str(departure.get("vehicle") or ""),
        direct="vias" not in item,
        destination=_destination(departure),
        train_type=_train_type(departure),
    )


def _exception_name(response: Any) -> Tuple[str, dict]:
    """iRail's ``exception`` field of an error answer, and the body."""
    try:
        body = response.json()
    except ValueError:
        return "", {}
    if not isinstance(body, dict):
        return "", {}
    return str(body.get("exception") or ""), body


def _station_not_found(response: Any) -> Optional[StationNotFound]:
    name, body = _exception_name(response)
    if name != "RequestedStopNotFoundException":
        return None
    message = str(body.get("message") or "Requested stop not found")
    parameter = None
    for name in ("from", "to"):
        if f"parameter '{name}'" in message:
            parameter = name
    return StationNotFound(message, parameter)


class IRailClient:
    def __init__(self, session: Any = None, timeout: Any = TIMEOUT):
        self.session = session or requests.Session()
        self.timeout = timeout

    def connections(
        self, from_id: str, to_id: str, results: int = RESULTS, lang: str = DEFAULT_LANGUAGE
    ) -> List[Connection]:
        params = {"from": from_id, "to": to_id, "format": "json", "results": results, "lang": lang}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            response = self.session.get(CONNECTIONS_URL, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise Unavailable(f"iRail request failed: {exc.__class__.__name__}") from exc

        if response.status_code == 400:
            not_found = _station_not_found(response)
            if not_found is not None:
                raise not_found
        if response.status_code == 404 and _exception_name(response)[0] == "JourneyPlanNotFoundException":
            # iRail is up and found no journey at all: no train, not no data.
            return []
        if response.status_code != 200:
            raise Unavailable(f"iRail answered HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError as exc:
            raise Unavailable("iRail answered invalid JSON") from exc
        items = body.get("connection") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise Unavailable("iRail answer has no connection list")

        connections = []
        for item in items:
            try:
                connections.append(parse_connection(item))
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping a malformed iRail connection")
        return connections
