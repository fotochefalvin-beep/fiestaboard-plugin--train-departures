"""Settings (CAP-1): offline station check, refresh interval, station picker, manifest."""

import socket

import pytest

from plugins.train_departures import stations


@pytest.fixture
def no_network(monkeypatch):
    """Any socket use fails the test: the station check must stay offline."""

    def refuse(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def plugin(manifest):
    from plugins.train_departures import Plugin

    return Plugin(manifest)


def config(**overrides):
    return dict({"from_station": "Gent-Sint-Pieters", "to_station": "Bruxelles-Central"}, **overrides)


@pytest.mark.parametrize(
    "name",
    ["Bruxelles-Central", "brussel-centraal", "Gent-Sint-Pieters", "Brussels-Central",
     "Brussel-Centraal/Bruxelles-Central", "BE.NMBS.008813003", "  luik-guillemins ", "Liège-Guillemins"],
)
def test_known_station_names_are_accepted(plugin, no_network, name):
    assert plugin.validate_config(config(from_station="Brugge", to_station=name)) == []


def test_misspelled_station_is_rejected_with_suggestion(plugin, no_network):
    errors = plugin.validate_config(config(from_station="Gnt"))
    assert errors == ["unknown station 'Gnt' — did you mean GENT-SINT-PIETERS?"]


@pytest.mark.parametrize("value", [None, "", "   ", 42])
def test_missing_station_is_required(plugin, value):
    assert plugin.validate_config(config(to_station=value)) == ["To station is required"]


def test_same_station_twice_is_rejected(plugin):
    errors = plugin.validate_config(config(to_station="Gand-Saint-Pierre"))
    assert errors == ["From and to station must be different"]


def test_labels_must_be_text(plugin):
    errors = plugin.validate_config(config(from_label=3, to_label=["x"]))
    assert errors == ["From label must be text", "To label must be text"]


@pytest.mark.parametrize("language", ["en", "nl", "fr", "de"])
def test_supported_languages_are_accepted(plugin, no_network, language):
    assert plugin.validate_config(config(language=language)) == []


@pytest.mark.parametrize("language", ["xx", "NL", "", 1, ["nl"]])
def test_unsupported_language_is_rejected_offline(plugin, no_network, language):
    assert plugin.validate_config(config(language=language)) == ["Language must be one of en, nl, fr, de"]


@pytest.mark.parametrize("walk", [None, 0, 10, 60])
def test_walk_time_in_range_is_accepted(plugin, walk):
    assert plugin.validate_config(config(walk_minutes=walk)) == []


@pytest.mark.parametrize("walk", [-1, 61, 10.5, 10.0, "10", True, [10]])
def test_walk_time_out_of_range_or_not_whole_is_rejected(plugin, walk):
    assert plugin.validate_config(config(walk_minutes=walk)) == [
        "Walk time must be a whole number of minutes from 0 to 60"
    ]


def host_validation(plugin, cfg):
    """What the registry runs on save: the base refresh check, then the plugin's."""
    return plugin._validate_refresh_seconds(cfg) + plugin.validate_config(cfg)


def test_refresh_below_30_is_rejected(plugin):
    assert host_validation(plugin, config(refresh_seconds=20)) == ["Refresh interval must be at least 30 seconds"]
    assert host_validation(plugin, config(refresh_seconds=30)) == []


def test_refresh_defaults_to_60(plugin):
    assert host_validation(plugin, config()) == []
    plugin.config = config()
    assert plugin.refresh_seconds == 60
    assert plugin.min_refresh_seconds == 30


# station picker ---------------------------------------------------------------


def request(query="", limit=200, options_id="stations"):
    from src.plugins.base import OptionsRequest

    return OptionsRequest(options_id=options_id, query=query, limit=limit)


def test_query_brux_returns_brussels_stations_offline(plugin, no_network):
    result = plugin.get_options(request("brux"))
    labels = [o.label for o in result.options]
    assert "Brussel-Centraal/Bruxelles-Central" in labels
    assert "Brussel-Zuid/Bruxelles-Midi" in labels
    assert all("Bruxelles" in label for label in labels)
    assert result.has_more is False
    assert result.total == len(labels)


def test_option_value_is_accepted_by_validate_config(plugin):
    option = plugin.get_options(request("gent sint", options_id="to_stations")).options[0]
    assert option.value == "Gent-Sint-Pieters"
    assert "Gand-Saint-Pierre" in option.description
    assert plugin.validate_config(config(from_station="Brugge", to_station=option.value)) == []


def test_empty_query_pages_the_whole_catalog(plugin):
    result = plugin.get_options(request("", limit=10))
    assert len(result.options) == 10
    assert result.has_more is True
    assert result.total == 716


def test_unknown_catalog_is_not_implemented(plugin):
    with pytest.raises(NotImplementedError):
        plugin.get_options(request(options_id="lines"))


def test_no_match_returns_empty(plugin):
    result = plugin.get_options(request("zzzzqqq"))
    assert result.options == [] and result.total == 0 and not result.has_more


# snapshot -----------------------------------------------------------------------


def test_snapshot_has_every_language_and_unique_keys():
    index = stations.load_index()
    assert len(index.stations) == 716
    assert index.resolve("Gand-Saint-Pierre").standardname == "Gent-Sint-Pieters"
    assert index.resolve("Ghent-Sint-Pieters").id == "BE.NMBS.008892007"
    assert index.resolve("Aken Hbf").standardname == "Aachen Hbf"
    assert index.resolve("008892007").id == "BE.NMBS.008892007"
    assert index.resolve("nowhere") is None


def test_inner_text_matches_come_after_word_starts():
    index = stations.StationIndex(
        [
            {"id": "BE.NMBS.1", "standardname": "Xantwerp", "names": ["Xantwerp"]},
            {"id": "BE.NMBS.2", "standardname": "Antwerp Y", "names": ["Antwerp Y"]},
        ]
    )
    assert [s.standardname for s in index.search("antw")] == ["Antwerp Y", "Xantwerp"]


@pytest.mark.parametrize(
    "typed, expected",
    [("Gnt", "Gent-Sint-Pieters"), ("Leuvn", "Leuven"), ("brusel centrl", "Brussel-Centraal/Bruxelles-Central")],
)
def test_suggestions(typed, expected):
    assert stations.load_index().suggest(typed).standardname == expected


def test_no_suggestion_for_punctuation_only(plugin):
    assert stations.load_index().suggest("--") is None
    assert plugin.validate_config(config(from_station="--")) == ["unknown station '--'"]


def test_station_properties():
    station = stations.load_index().resolve("Brussel-Zuid")
    assert station.first_name == "Brussel-Zuid"
    assert station.is_hub
    assert not stations.load_index().resolve("Gent-Dampoort").is_hub


# manifest -----------------------------------------------------------------------


def test_manifest_is_valid_for_the_host(manifest):
    from src.plugins.manifest import validate_manifest

    ok, errors = validate_manifest(manifest)
    assert ok, errors


def test_manifest_declares_eleven_variables_of_15(manifest):
    simple = manifest["variables"]["simple"]
    assert set(simple) == {
        "status", "route", "from_label", "to_label", "time", "delay", "platform", "destination",
        "next_time", "next_delay", "next_destination",
    }
    assert simple["destination"]["group"] == "next_train"
    assert simple["next_destination"]["group"] == "following_train"
    assert all(v["max_length"] == 15 for v in simple.values())
    assert manifest["max_lengths"] == {k: 15 for k in simple}
    assert manifest["min_refresh_seconds"] == 30
    props = manifest["settings_schema"]["properties"]
    assert props["refresh_seconds"]["default"] == 60
    assert props["refresh_seconds"]["minimum"] == 30
    assert props["language"]["enum"] == ["en", "nl", "fr", "de"]
    assert len(props["language"]["enumNames"]) == 4
    assert props["language"]["default"] == "nl"
    walk = props["walk_minutes"]
    assert (walk["type"], walk["default"], walk["minimum"], walk["maximum"]) == ("integer", 0, 0, 60)
    assert "walk_minutes" not in manifest["settings_schema"]["required"]
    assert manifest["version"] == "0.3.0"
    for key in ("from_station", "to_station"):
        assert props[key]["ui:widget"] == "remote-options"
        assert props[key]["ui:options"]["server_search"] is True
