# Train Departures

A [FiestaBoard](https://github.com/Fiestaboard/FiestaBoard) plugin that shows
the next direct NMBS/SNCB train on your route, with its delay, platform and
final destination, plus the train after it. Times come from [iRail](https://api.irail.be)
`/connections` (free, no key) and are refreshed all day.

```
08:14 +12   P4
EUPEN
NEXT 08:44
```

## How it picks the train

- Only **direct** trains: connections with a transfer are skipped.
- Cancelled trains and trains that have left (iRail's `left` flag, or
  scheduled time + delay already past) are never shown.
- The shown train is the one that will **actually** leave first: an 08:14
  train running +35 is shown after an on-time 08:44.
- With a **walk time** set, a train is dropped once you can no longer
  catch it: when scheduled time + delay − walk time is past. With a 10-minute
  walk, an on-time 08:26 makes way for the next train just after 08:16;
  running +5, just after 08:21 (at the first refresh past that time). With 0
  (the default) a train stays until it departs.

When iRail cannot be reached (timeout, connection error, HTTP error, bad
answer), `status` becomes `NO TRAIN DATA` and the train fields are empty,
instead of showing stale times; the next good answer brings `OK` back. When
iRail answers but no direct train is among the results, `status` is
`NO DIRECT TRAIN`; with a walk time this also covers direct trains that are all
too close to catch.

## Setup

1. Install the plugin in FiestaBoard and open its settings.
2. Pick the **From station** and **To station**: start typing to search the
   station list (Dutch, French, English or German names all work, e.g.
   `Gent-Sint-Pieters`, `Brussel-Centraal`, `Bruxelles-Central`).
3. Optionally set short **labels** for the board and the **language** of the
   destination names, then enable the plugin.
4. Make sure FiestaBoard's general timezone setting is `Europe/Brussels`
   (a fresh install defaults to `America/Los_Angeles`, which would show the
   times nine hours off).
5. Build a page from the variables below (there is no default layout).

## Settings

| Setting | Default | Notes |
|---|---|---|
| `from_station` | – | Station picker. A typed name is checked offline against the bundled station list; a misspelling is rejected with a suggestion (`unknown station 'Gnt' — did you mean GENT-SINT-PIETERS?`). |
| `to_station` | – | As above. Must differ from the from station. |
| `from_label` | empty | Short board name (`GENT`). Empty: the station's first standard name, cut to 15 characters. |
| `to_label` | empty | Short board name (`BRUSSEL-C`). Same fallback. |
| `language` | `nl` | `en`, `nl`, `fr` or `de`: the language iRail uses for the destination names (`LUIK-GUILLEMINS` in `nl`, `LIEGE-GUILLEMIN` in `fr`). Labels are not affected. |
| `walk_minutes` | 0 | Whole minutes from home to the platform, 0 to 60. A train you can no longer catch (scheduled time + delay − walk time past) is not shown, neither as `time` nor as `next_time`. |
| `refresh_seconds` | 60 | How often to ask iRail; at least 30. |

If iRail stops knowing a station from the bundled list, the plugin reports a
settings error naming that station: pick it again in the settings.

## Variables

Every value is upper-case ASCII, accents stripped, at most 15 characters
(a Vestaboard Note is 3 × 15).

| Variable | Values | Example |
|---|---|---|
| `status` | `OK`, `NO TRAIN DATA`, `NO DIRECT TRAIN`; the train fields (`time` to `next_destination`) are empty unless `OK` | `OK` |
| `route` | `<from_label>><to_label>`, cut to 15 | `GENT>BRUSSEL-C` |
| `from_label` | from the setting | `GENT` |
| `to_label` | from the setting | `BRUSSEL-C` |
| `time` | scheduled departure, local `HH:MM`, of the next train you can still catch | `08:14` |
| `delay` | `+N` whole minutes; empty when on time | `+12` |
| `platform` | `P` + platform; empty when unknown | `P4` |
| `destination` | where that train ends (iRail's direction, in the `language` setting), cut to 15; empty when iRail gives none | `EUPEN` |
| `next_time` | scheduled departure of the following direct train; empty if none | `08:44` |
| `next_delay` | as `delay`, for the following train | `+3` |
| `next_destination` | as `destination`, for the following train | `WELKENRAEDT` |

Times are scheduled times in FiestaBoard's general timezone. A fresh
FiestaBoard install defaults to `America/Los_Angeles`, so set the general
timezone to `Europe/Brussels` in FiestaBoard's general settings; Europe/Brussels
is used only when that setting is empty or invalid. At night the next train may be tomorrow; only its time is
shown.

### Example Note layout

```
{{train_departures.time}} {{train_departures.delay}}   {{train_departures.platform}}
{{train_departures.destination}}
NEXT {{train_departures.next_time}}
```

The destination is what the station display and platform announcements use to
name the train. Swap it for `{{train_departures.route}}` if you prefer to see
your route.

When `status` is not `OK`, show `{{train_departures.status}}` instead.

## Development

### Station snapshot

`stations.json` is a snapshot of iRail `/stations` in English, Dutch, French
and German, merged by station id. The plugin never fetches the station list
at runtime, so saving settings and searching stations work offline. Refresh
it now and then and commit the result:

```bash
python3 scripts/refresh_stations.py
```

### Plugin tests

The tests run against a checkout of
[FiestaBoard core](https://github.com/Fiestaboard/FiestaBoard) at `v9.13.0`,
with this repo linked as `plugins/train_departures`. They never touch the
network (HTTP is faked; recorded iRail answers are in `tests/fixtures/`).
`.github/workflows/ci.yml` is the canonical recipe; locally, from the folder
that *contains* this repo:

```bash
git clone --branch v9.13.0 https://github.com/Fiestaboard/FiestaBoard.git fiestaboard-core
python3.11 -m venv .venv-fb && source .venv-fb/bin/activate
pip install -r fiestaboard-core/requirements.txt pytest pytest-cov

mkdir -p plugins && touch plugins/__init__.py
ln -s ../fiestaboard-plugin--train-departures plugins/train_departures

cd fiestaboard-plugin--train-departures
PYTHONPATH="$(cd .. && pwd):$(cd ../fiestaboard-core && pwd)" \
BOARD_READ_WRITE_KEY=test_key \
python -m pytest tests/ -v --cov --cov-report=term-missing
```

Coverage is measured on the plugin package (`__init__.py`, `stations.py`,
`irail.py`) and must be at least 80% (`.coveragerc`). CI also runs the host's
`validate_manifest` and install check. There is deliberately no
`requirements.txt` in this repo: the host's install check scans the tree for
one, and the only dependency (`requests`) comes with FiestaBoard.

## Credits

Train data: [iRail](https://irail.be), a volunteer-run open API for NMBS/SNCB
data. Requests identify this plugin and its repository in the `User-Agent`.

## License

MIT, see [LICENSE](LICENSE).
