"""Test fixtures for the Train Departures plugin.

The tests run against a FiestaBoard core checkout (see README, "Plugin
tests"): ``src.*`` must be importable and this repo linked as
``plugins/train_departures``. Host imports happen inside fixtures (lazily),
so collecting this file does not need the core on the path.

No test touches the network: HTTP goes through :class:`FakeSession`, and the
recorded iRail answers live in ``tests/fixtures/``.
"""

import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "manifest.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
BRUSSELS = ZoneInfo("Europe/Brussels")

GENT = "BE.NMBS.008892007"
BRUSSEL_C = "BE.NMBS.008813003"


def load_fixture(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def at(hhmm, seconds=0):
    """Epoch seconds of *hhmm* on 4 Nov 2026, Brussels time."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 4, hour, minute, seconds, tzinfo=BRUSSELS).timestamp()


def connection(hhmm, delay_min=0, platform="4", canceled=False, left=False, vias=False, vehicle=None):
    """One connection, shaped like the first record of the recorded answer."""
    item = copy.deepcopy(load_fixture("connections_gent_brussel.json")["connection"][0])
    item.pop("vias", None)
    dep = item["departure"]
    dep["time"] = str(int(at(hhmm)))
    dep["delay"] = str(delay_min * 60)
    dep["platform"] = platform
    dep["canceled"] = "1" if canceled else "0"
    dep["left"] = "1" if left else "0"
    dep["vehicle"] = vehicle or f"BE.NMBS.IC{hhmm.replace(':', '')}"
    if vias:
        item["vias"] = copy.deepcopy(load_fixture("connections_gent_brussel.json")["connection"][2]["vias"])
    return item


def body(*connections):
    return {"version": "1.4", "timestamp": "1793799182", "connection": list(connections)}


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    """``requests.Session`` stand-in: records every call, answers from a queue.

    Each answer is a FakeResponse, an exception to raise, or a callable
    returning one; the last answer repeats.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(answer) and not isinstance(answer, FakeResponse):
            answer = answer()
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def manifest():
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def now_holder():
    return {"now": at("08:00")}


@pytest.fixture
def make_plugin(manifest, now_holder, monkeypatch):
    """An enabled plugin on Gent-Sint-Pieters -> Brussel-Centraal, fake HTTP and clock.

    Returns ``make(session, config=None)``. Local time is Europe/Brussels.
    """
    from plugins.train_departures import Plugin, irail

    monkeypatch.setattr("plugins.train_departures.local_zone", lambda: BRUSSELS)

    def make(session, config=None):
        plugin = Plugin(manifest)
        plugin._now = lambda: now_holder["now"]
        plugin.client_factory = lambda: irail.IRailClient(session=session)
        plugin.config = dict({"from_station": "Gent-Sint-Pieters", "to_station": "Brussel-Centraal"}, **(config or {}))
        plugin.enabled = True
        return plugin

    return make
