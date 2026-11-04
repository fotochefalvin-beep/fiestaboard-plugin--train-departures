"""Train selection, formatting and status (CAP-2 to CAP-5), labels (CAP-1)."""

import pytest
import requests

from .conftest import (
    BRUSSEL_C,
    GENT,
    FakeResponse,
    FakeSession,
    at,
    body,
    connection,
    load_fixture,
)

TRAIN_FIELDS = ("time", "delay", "platform", "destination", "next_time", "next_delay", "next_destination")


def ok(*connections):
    return FakeResponse(200, body(*connections))


def fetch(make_plugin, *answers, config=None):
    session = FakeSession(*answers)
    plugin = make_plugin(session, config)
    return plugin.fetch_data(), session, plugin


# CAP-2 / CAP-4 ----------------------------------------------------------------


def test_next_and_following_train(make_plugin):
    result, session, _ = fetch(make_plugin, ok(connection("08:14", 12, "4"), connection("08:44")))
    assert result.available
    d = result.data
    assert (d["status"], d["time"], d["delay"], d["platform"]) == ("OK", "08:14", "+12", "P4")
    assert (d["next_time"], d["next_delay"]) == ("08:44", "")


def test_request_uses_station_ids_user_agent_and_timeout(make_plugin):
    _, session, _ = fetch(make_plugin, ok(connection("08:14")))
    call = session.calls[0]
    assert call["url"] == "https://api.irail.be/connections/"
    assert call["params"] == {"from": GENT, "to": BRUSSEL_C, "format": "json", "results": 6, "lang": "nl"}
    ua = call["headers"]["User-Agent"]
    assert "train-departures" in ua and "https://github.com/" in ua and "@" not in ua
    assert call["timeout"]


def test_on_time_train_has_empty_delay_and_missing_platform_is_empty(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("08:14", 0, "?"), connection("08:44", 3, "")))
    d = result.data
    assert (d["delay"], d["platform"]) == ("", "")
    assert d["next_delay"] == "+3"


@pytest.mark.parametrize("platform", ["→", "  ", "?", ""])
def test_platform_with_nothing_printable_is_empty(platform):
    from plugins.train_departures import format_platform

    assert format_platform(platform) == ""


def test_only_one_direct_train_leaves_following_empty(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("08:14", 0, "4"), connection("08:20", vias=True)))
    d = result.data
    assert d["time"] == "08:14"
    assert (d["next_time"], d["next_delay"]) == ("", "")


def test_recorded_answer_skips_the_via_duplicate(make_plugin, now_holder):
    now_holder["now"] = 1793799182  # the recorded answer's timestamp, 14:33
    result, _, _ = fetch(make_plugin, FakeResponse(200, load_fixture("connections_gent_brussel.json")))
    d = result.data
    assert (d["status"], d["time"], d["delay"], d["platform"]) == ("OK", "14:41", "", "P10")
    assert d["next_time"] == "14:56"
    assert (d["destination"], d["next_destination"]) == ("LIEGE-GUILLEMIN", "WELKENRAEDT")


# destination --------------------------------------------------------------------


def test_both_destinations_are_board_text(make_plugin):
    result, _, _ = fetch(
        make_plugin,
        ok(connection("08:14", destination="Liège-Guillemins"), connection("08:44", destination="Eupen")),
    )
    d = result.data
    assert (d["destination"], d["next_destination"]) == ("LIEGE-GUILLEMIN", "EUPEN")


@pytest.mark.parametrize(
    "direction",
    [None, {}, {"name": ""}, {"name": None}, "Eupen", ["Eupen"], "absent"],
    ids=["null", "no-name", "empty-name", "null-name", "string", "list", "absent"],
)
def test_missing_destination_is_empty_and_harmless(make_plugin, direction):
    first = connection("08:14", 12, "4")
    if direction == "absent":
        del first["departure"]["direction"]
    else:
        first["departure"]["direction"] = direction
    result, _, _ = fetch(make_plugin, ok(first, connection("08:44", destination="Eupen")))
    d = result.data
    assert d["destination"] == ""
    assert (d["status"], d["time"], d["delay"], d["platform"]) == ("OK", "08:14", "+12", "P4")
    assert (d["next_time"], d["next_destination"]) == ("08:44", "EUPEN")


def test_one_direct_train_leaves_next_destination_empty(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("08:14", destination="Eupen"), connection("08:20", vias=True)))
    assert (result.data["destination"], result.data["next_destination"]) == ("EUPEN", "")


def test_destination_follows_the_reordered_trains(make_plugin):
    result, _, _ = fetch(
        make_plugin,
        ok(connection("08:14", 35, destination="Eupen"), connection("08:44", destination="Welkenraedt")),
    )
    d = result.data
    assert (d["time"], d["destination"]) == ("08:44", "WELKENRAEDT")
    assert (d["next_time"], d["next_destination"]) == ("08:14", "EUPEN")


@pytest.mark.parametrize("config, lang", [({}, "nl"), ({"language": "fr"}, "fr"), ({"language": "en"}, "en")])
def test_language_setting_is_sent_as_lang(make_plugin, config, lang):
    _, session, _ = fetch(make_plugin, ok(connection("08:14")), config=config)
    assert session.calls[0]["params"]["lang"] == lang


def test_invalid_stored_language_falls_back_to_nl(make_plugin):
    _, session, _ = fetch(make_plugin, ok(connection("08:14")), config={"language": "xx"})
    assert session.calls[0]["params"]["lang"] == "nl"


# CAP-3 --------------------------------------------------------------------------


def test_delayed_train_is_replaced_by_one_that_leaves_first(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("08:14", 35), connection("08:44")))
    d = result.data
    assert (d["time"], d["delay"]) == ("08:44", "")
    assert (d["next_time"], d["next_delay"]) == ("08:14", "+35")


@pytest.mark.parametrize("seconds, expected", [(0, "08:26"), (30, "08:39")])
def test_departure_edge(make_plugin, now_holder, seconds, expected):
    now_holder["now"] = at("08:26", seconds)
    result, _, _ = fetch(make_plugin, ok(connection("08:26"), connection("08:39")))
    assert result.data["time"] == expected


@pytest.mark.parametrize(
    "skipped",
    [
        connection("08:10", canceled=True),
        connection("08:10", vias=True),
        connection("07:50", 5),  # 07:55 expected, now 08:00: departed
        connection("08:10", left=True),
    ],
    ids=["cancelled", "via", "departed", "left"],
)
def test_skipped_connections_are_never_chosen(make_plugin, skipped):
    result, _, _ = fetch(make_plugin, ok(skipped, connection("08:30")))
    assert result.data["time"] == "08:30"
    assert result.data["next_time"] == ""


def test_delayed_train_still_to_leave_stays(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("07:55", 10), connection("08:30")))
    assert (result.data["time"], result.data["delay"]) == ("07:55", "+10")


def test_same_vehicle_and_time_is_shown_once(make_plugin):
    twin = connection("08:14", vehicle="BE.NMBS.IC1")
    result, _, _ = fetch(make_plugin, ok(twin, dict(twin)))
    assert result.data["next_time"] == ""


# statuses -----------------------------------------------------------------------


def test_no_direct_train(make_plugin):
    result, _, _ = fetch(
        make_plugin,
        ok(connection("08:14", vias=True, destination="Eupen"), connection("08:20", canceled=True, destination="Eupen")),
    )
    assert result.available
    assert result.data["status"] == "NO DIRECT TRAIN"
    assert all(result.data[f] == "" for f in TRAIN_FIELDS)


def test_empty_connection_list_is_no_direct_train(make_plugin):
    result, _, _ = fetch(make_plugin, ok())
    assert result.data["status"] == "NO DIRECT TRAIN"


def test_journey_plan_not_found_is_no_direct_train(make_plugin):
    result, _, _ = fetch(make_plugin, FakeResponse(404, load_fixture("journey_not_found.json")))
    assert result.available
    assert result.data["status"] == "NO DIRECT TRAIN"


@pytest.mark.parametrize(
    "answer",
    [
        requests.Timeout("slow"),
        requests.ConnectionError("down"),
        FakeResponse(503, {}),
        FakeResponse(200, ValueError("bad json")),
        FakeResponse(200, {"no": "connections"}),
        FakeResponse(400, {"exception": "SomethingElse"}),
        FakeResponse(400, ValueError("html")),
        FakeResponse(404, ["not", "an", "object"]),
    ],
    ids=["timeout", "connection", "5xx", "bad-json", "no-list", "other-400", "400-not-json", "404-other"],
)
def test_irail_down_says_no_train_data_then_recovers(make_plugin, answer):
    session = FakeSession(answer, ok(connection("08:14", 12)))
    plugin = make_plugin(session)
    first = plugin.fetch_data()
    assert first.available
    assert first.data["status"] == "NO TRAIN DATA"
    assert all(first.data[f] == "" for f in TRAIN_FIELDS)
    assert first.data["route"] == "GENT-SINT-PIETE"
    second = plugin.fetch_data()
    assert (second.data["status"], second.data["time"]) == ("OK", "08:14")


@pytest.mark.parametrize("parameter, named", [("to", "Brussel-Centraal"), ("from", "Gent-Sint-Pieters")])
def test_station_gone_is_a_settings_error(make_plugin, parameter, named):
    gone = load_fixture("stop_not_found.json")
    gone["message"] = gone["message"].replace("parameter 'to'", f"parameter '{parameter}'")
    result, _, _ = fetch(make_plugin, FakeResponse(400, gone))
    assert not result.available
    assert named in result.error
    assert "settings" in result.error
    other = "Gent-Sint-Pieters" if parameter == "to" else "Brussel-Centraal/"
    assert other not in result.error


def test_station_gone_without_parameter_names_both(make_plugin):
    gone = {"exception": "RequestedStopNotFoundException", "message": "Requested stop not found"}
    result, _, _ = fetch(make_plugin, FakeResponse(400, gone))
    assert "Gent-Sint-Pieters" in result.error and "Brussel-Centraal" in result.error


def test_malformed_connection_is_skipped(make_plugin):
    broken = {"id": "9", "departure": {"time": "soon"}}
    result, _, _ = fetch(make_plugin, ok(broken, connection("08:14")))
    assert result.data["time"] == "08:14"


def test_unknown_station_in_config_is_unavailable(make_plugin):
    result, session, _ = fetch(make_plugin, ok(), config={"to_station": "Nowhere"})
    assert not result.available
    assert "To station" in result.error
    assert session.calls == []


# labels (CAP-1) -----------------------------------------------------------------


def test_empty_labels_fall_back_to_first_standard_name(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("08:14")), config={"from_label": "", "to_label": "  "})
    d = result.data
    assert d["from_label"] == "GENT-SINT-PIETE"
    assert d["to_label"] == "BRUSSEL-CENTRAA"
    assert d["route"] == "GENT-SINT-PIETE"


def test_labels_are_board_text(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("08:14")), config={"from_label": "Gent", "to_label": "Brussel-C"})
    d = result.data
    assert (d["from_label"], d["to_label"], d["route"]) == ("GENT", "BRUSSEL-C", "GENT>BRUSSEL-C")


def test_labels_strip_accents_and_cut_to_15(make_plugin):
    config = {"from_label": "Liège-Guillemins long", "to_label": "Bruxelles-Midi"}
    result, _, _ = fetch(make_plugin, ok(connection("08:14")), config=config)
    d = result.data
    assert d["from_label"] == "LIEGE-GUILLEMIN"
    assert d["route"] == "LIEGE-GUILLEMIN"
    assert all(len(v) <= 15 and v == v.upper() and v.isascii() for v in d.values())


def test_every_value_fits_the_note(make_plugin):
    result, _, _ = fetch(make_plugin, ok(connection("08:14", 125, "12A"), connection("10:44", 3)))
    assert result.data["delay"] == "+125"
    assert result.data["platform"] == "P12A"
    assert all(len(v) <= 15 and v == v.upper() and v.isascii() for v in result.data.values())


# caching and time zone ----------------------------------------------------------


def test_config_change_clears_cache(make_plugin):
    session = FakeSession(ok(connection("08:14")))
    plugin = make_plugin(session)
    plugin.get_data()
    plugin.get_data()
    assert len(session.calls) == 1
    plugin.config = dict(plugin.config, to_label="BXL")
    assert plugin.get_data().data["to_label"] == "BXL"
    assert len(session.calls) == 2


@pytest.mark.parametrize("name", ["", "Not/AZone"])
def test_local_zone_falls_back_to_brussels(monkeypatch, name):
    import plugins.train_departures as td
    import src.config

    class FakeConfig:
        GENERAL_TIMEZONE = name

    monkeypatch.setattr(src.config, "Config", FakeConfig)
    assert str(td.local_zone()) == "Europe/Brussels"


def test_local_zone_reads_config(monkeypatch):
    import plugins.train_departures as td
    import src.config

    class FakeConfig:
        GENERAL_TIMEZONE = "Europe/London"

    monkeypatch.setattr(src.config, "Config", FakeConfig)
    assert str(td.local_zone()) == "Europe/London"


def test_default_client_is_irail_with_real_session(manifest):
    from plugins.train_departures import Plugin, irail

    plugin = Plugin(manifest)
    client = plugin._get_client()
    assert isinstance(client, irail.IRailClient)
    assert isinstance(client.session, requests.Session)
    assert plugin._get_client() is client
    assert plugin.plugin_id == "train_departures"
