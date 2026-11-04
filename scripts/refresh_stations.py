#!/usr/bin/env python3
"""Refresh the bundled iRail station snapshot (``stations.json``).

The plugin checks station names offline against this snapshot, so it is
refreshed here by hand, never at runtime. Run from the repo root:

    python3 scripts/refresh_stations.py

It fetches iRail ``/stations`` in English, Dutch, French and German, merges
the four lists by station id and writes ``stations.json`` next to the plugin:
one record per station with its ``id``, ``standardname`` and every name it
goes by. Commit the result.
"""

import json
import sys
from pathlib import Path

import requests

STATIONS_URL = "https://api.irail.be/stations/"
LANGUAGES = ("en", "nl", "fr", "de")
USER_AGENT = (
    "fiestaboard-plugin--train-departures/scripts "
    "(+https://github.com/fotochefalvin-beep/fiestaboard-plugin--train-departures)"
)
TIMEOUT_SECONDS = 30
OUTPUT = Path(__file__).resolve().parent.parent / "stations.json"


def fetch(lang: str) -> list:
    response = requests.get(
        STATIONS_URL,
        params={"format": "json", "lang": lang},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["station"]


def merge(lists: dict) -> list:
    stations: dict = {}
    for lang in LANGUAGES:
        for item in lists[lang]:
            record = stations.setdefault(
                item["id"], {"id": item["id"], "standardname": item["standardname"], "names": []}
            )
            for name in (item.get("name"), item.get("standardname")):
                if name and name not in record["names"]:
                    record["names"].append(name)
    return sorted(stations.values(), key=lambda s: (s["standardname"].casefold(), s["id"]))


def main() -> int:
    stations = merge({lang: fetch(lang) for lang in LANGUAGES})
    lines = ",\n".join(json.dumps(s, ensure_ascii=False) for s in stations)
    OUTPUT.write_text("[\n" + lines + "\n]\n", encoding="utf-8")
    print(f"wrote {len(stations)} stations to {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
