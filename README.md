# OpenAQ Air Quality Plugin for Domoticz

**Version 4.1**

A Domoticz Python plugin that reads the latest air-quality measurements for one monitoring location from [OpenAQ](https://openaq.org) (API v3) and exposes them as native Domoticz devices. The plugin is read-only: it only ever sends `GET` requests.

OpenAQ aggregates PM<sub>2.5</sub>, PM<sub>10</sub>, ozone (O<sub>3</sub>), sulfur dioxide (SO<sub>2</sub>), nitrogen dioxide (NO<sub>2</sub>), carbon monoxide (CO) and black carbon (BC) from real-time government and research-grade sources.

With special recognition to Xorfor for creating this plugin, and to waltervl for its evolution. From v3.1 an OpenAQ API key is required; v4.0 moved the plugin to OpenAQ API v3.

## Highlights

- Uses **OpenAQ API v3**. The v1/v2 endpoints were retired by OpenAQ on 2025-01-31 and now answer HTTP 410 Gone.
- One OpenAQ location per hardware instance: either an explicit **Location ID**, or a one-time lookup of a location near your Domoticz coordinates.
- Seven pollutant sensors (BC, CO, NO<sub>2</sub>, O<sub>3</sub>, PM<sub>10</sub>, PM<sub>2.5</sub>, SO<sub>2</sub>), shown with the unit OpenAQ reports.
- An **Air Quality Index** (0–4) and a native Domoticz **Alert** device (gray, green, yellow, orange, red) based on the [airqualitynow.eu](http://www.airqualitynow.eu/about_indices_definition.php) limits.
- An **Info** text device with the location ID and the number of sensors reporting.
- **Secrets stay out of the log.** The API-key field is masked in the Domoticz hardware form, and neither the key nor your Domoticz location is written to the log, not even with Debug on.
- A failed start leaves the plugin idle with a clear message instead of an error on every heartbeat.
- Polls once per hour, far below OpenAQ's free-tier limits (60 requests/minute, 2,000/hour).
- Python standard library only; no `pip install` and no third-party runtime dependencies.

## Requirements

- Domoticz with Python plugin support.
- Python 3 (the interpreter used by Domoticz).
- An OpenAQ API key (free). Create an account at <https://explore.openaq.org/register>; the key is shown, and can be rotated, at <https://explore.openaq.org/account>.
- Network access from the Domoticz host to `api.openaq.org` on port 443 (HTTPS).
- An OpenAQ location near you. Check <https://explore.openaq.org> to see whether a source exists at your location.
- Your position set in Domoticz under **Setup → Settings → System → Location**, but only if you leave the Location ID blank (see [Choosing a location](#choosing-a-location)).

## Installation

Clone the repository into the Domoticz `plugins` directory:

```bash
cd <domoticz>/plugins
git clone https://github.com/janreimen/Domoticz-OpenAQ-Plugin.git
```

The directory must contain `plugin.py` and `xfr_openaq2.zip` (the device icons). Restart Domoticz after installing or updating the plugin. See [`DEPLOY.md`](DEPLOY.md) for permissions, Docker, updating, rollback and troubleshooting.

Upgrading from 4.0 needs no other action: the devices keep their unit numbers and history, and the API-key field simply becomes masked.

## Configure the Domoticz hardware

Add a new hardware instance of type **OpenAQ 4** under **Setup → Hardware**.

| Field | Description |
|---|---|
| Radius (km, max 25, used only if Location ID is blank) | Search radius for the one-time lookup of a nearby location. Default `10`. Decimals are accepted (`2.5` or `2,5`). A value above OpenAQ's maximum of 25 km is reduced to 25; an empty, non-numeric or non-positive value uses `10`. The radius used is written to the log. |
| OpenAQ API-KEY | Required. Your key from <https://explore.openaq.org/account>. The field is masked. The key is sent in the `X-API-Key` header, only to `api.openaq.org`. |
| OpenAQ Location ID (blank = auto-detect) | Numeric OpenAQ location ID (a positive number), for example `9645` (Beidweiler, LU). Leave blank to let the plugin pick a location once at startup. |
| Debug | `True` or `False` (default). Debug logging is verbose. The API key and your location are masked in it, but review a log before sharing it; see [`SECURITY.md`](SECURITY.md). |

The plugin uses the latitude and longitude from **Setup → Settings → System → Location** only for the automatic lookup. With a Location ID entered, the Domoticz location is not read at all.

### Choosing a location

With a Location ID set, the plugin always reads exactly that location. With the field blank, the plugin asks OpenAQ for **one** location within the radius of your Domoticz coordinates and logs what it used and chose:

```text
Looking for an OpenAQ location within 10 km of the Domoticz location
Auto-selected OpenAQ location '<name>' (id <id>): the first location OpenAQ returned within 10 km, which is not necessarily the nearest one.
To keep using this location, enter <id> in the 'OpenAQ Location ID' hardware field.
```

OpenAQ's API documentation does not state that results are ordered by distance, so the location returned is not guaranteed to be the closest one. For a predictable result, copy the logged ID into the **OpenAQ Location ID** field.

## Device units

| Unit | Device | Type | Description |
|---:|---|---|---|
| 1 | BC | Custom sensor | Black carbon |
| 2 | CO | Custom sensor | Carbon monoxide |
| 3 | NO<sub>2</sub> | Custom sensor | Nitrogen dioxide |
| 4 | O<sub>3</sub> | Custom sensor | Ozone |
| 5 | PM<sub>10</sub> | Custom sensor | Particulate matter less than 10 micrometres in diameter |
| 6 | PM<sub>2.5</sub> | Custom sensor | Particulate matter less than 2.5 micrometres in diameter |
| 7 | SO<sub>2</sub> | Custom sensor | Sulfur dioxide |
| 8 | Info | Text | `OpenAQ Location ID: <id>` and `Sensors reporting: <n>/<m>` |
| 9 | Pollutants | Alert (General / Alert) | Overall alert level 0–4 and the pollutants behind it; `No alert` at level 0 |
| 10 | Air Quality Index | Custom sensor | Overall level 0–4 as a number |

Units 1–10 are unchanged since 4.0, and a test guards their definitions, so upgrades keep existing devices and their history.

Domoticz prefixes each device name with the hardware name, as in the screenshot below.

![OpenAQ devices](./images/Knipsel.PNG)

*The screenshot dates from an earlier version of the plugin. Since 4.0 the Info device shows the OpenAQ location ID and the number of sensors reporting instead of the number of stations and measurements.*

### How readings are processed

- OpenAQ's `latest` endpoint returns one value per sensor without naming the pollutant. At startup the plugin makes one extra call to `GET /v3/locations/{id}` to learn which sensor measures which pollutant and in which unit. Sensors for parameters the plugin does not track (for example temperature) are ignored.
- For each pollutant the reading with the **newest timestamp** wins. Values of **zero or below** (including the `-999` "no data" sentinel) are ignored, so a genuine reading of exactly 0 is not shown.
- Values are shown with one decimal place. The unit label (typically µg/m³) is taken from OpenAQ and applied to the device on every update.
- In **Sensors reporting: n/m**, `n` is the number of pollutant readings that were used and `m` is the total number of readings OpenAQ returned for the location, including parameters the plugin does not track.
- The plugin polls OpenAQ about once per hour.

## Air Quality Index

The **Pollutants** alert and the **Air Quality Index** device show how the measured values compare with the limits from <http://www.airqualitynow.eu/about_indices_definition.php>. The table gives the upper limit of each band; a value above a limit moves that pollutant into the next band.

| Pollutant | Very low | Low | Medium | High | Very high |
|:---|---:|---:|---:|---:|---:|
| **CO** | 5000 | 7500 | 10000 | 20000 | > 20000 |
| **NO<sub>2</sub>** | 50 | 100 | 200 | 400 | > 400 |
| **O<sub>3</sub>** | 60 | 120 | 180 | 240 | > 240 |
| **PM<sub>10</sub>** | 25 | 50 | 90 | 180 | > 180 |
| **PM<sub>2.5</sub>** | 15 | 30 | 55 | 110 | > 110 |
| **SO<sub>2</sub>** | 50 | 100 | 350 | 500 | > 500 |
| | gray | green | yellow | orange | red |

The level is `0` (Very low, gray) to `4` (Very high, red). The overall level is the highest level reached by any pollutant. Black carbon has no limits and never raises the level.

## Known limitations

- **Nearest location is not guaranteed.** See [Choosing a location](#choosing-a-location); set the Location ID explicitly if it matters.
- **Startup lookups are not retried.** If OpenAQ or the network is unreachable when the plugin starts (for example when Domoticz boots before the network is up), the plugin logs the error and stays idle until the hardware is restarted or updated under **Setup → Hardware**. Entering a Location ID saves one of the two lookups.
- **No unit conversion.** The limits above are in µg/m³ and are compared with the raw number OpenAQ reports. If a location reports another unit (ppm or ppb, for example), the level is not meaningful.
- **Zero readings are ignored,** as described above.
- **No staleness handling.** If a sensor stops reporting, its last value stays on the device until a newer reading arrives.
- **Pollutants text.** The text lists the pollutants that raised the overall level while the plugin walked its list (BC, CO, NO<sub>2</sub>, O<sub>3</sub>, PM<sub>10</sub>, PM<sub>2.5</sub>, SO<sub>2</sub>). A lower-level pollutant can therefore be listed, and a pollutant tied at the top level can be left out. The numeric level is always the true maximum.

Planned and potential work is tracked in [`ROADMAP.md`](ROADMAP.md).

## Troubleshooting

| Log message | Meaning |
|---|---|
| `Unable to read openaq API-KEY from settings` | The API key field is empty. |
| `OpenAQ Location ID must be numeric, got '<value>'` | Enter digits only, or leave the field blank. |
| `OpenAQ Location ID must be a positive number, got '<value>'` | Zero and negative IDs do not exist. Enter a real location ID or leave the field blank. |
| `The Domoticz location is not set or not valid` | Only raised for the automatic lookup. Set **Setup → Settings → System → Location**, or enter a Location ID. |
| `Radius '<value>' is not a positive number; using the default of 10 km` | Informational. The radius field is not a positive number. |
| `Radius is empty; using the default of 10 km` | Informational. |
| `Radius <value> km is above the OpenAQ maximum of 25 km; using 25 km` | Informational. OpenAQ accepts at most 25 km. |
| `No OpenAQ location found within <n> km of the Domoticz location` | Nothing found within the radius, or the lookup failed (see the preceding error). Increase the radius (up to 25 km) or enter a Location ID. |
| `OpenAQ returned a location without an ID` | OpenAQ's answer was unusable. Enter a Location ID. |
| `OpenAQ request <path> returned HTTP <status>` | OpenAQ rejected a startup request. Check the API key and the Location ID. |
| `OpenAQ request <path> failed: <error>` | Network, DNS, TLS or timeout problem between Domoticz and `api.openaq.org`. |
| `Could not fetch sensor list for OpenAQ location <id>` | The lookup of the location failed. Check the ID and look for an earlier error. |
| `Location <id> returned no usable sensors` | The location has no sensors the plugin can read. Pick another location. |
| `OpenAQ plugin not started` | Always follows one of the errors above. The plugin stays idle until the hardware is restarted or updated. |
| `openaq returned HTTP 410 Gone` | OpenAQ retired an API path this plugin version uses. Update the plugin. |
| `openaq returned a status: <status>` | OpenAQ answered the hourly poll with an error, for example 429 (rate limit reached). Wait for the next period. |

More detail is in [`DEPLOY.md`](DEPLOY.md).

## Development and tests

The project has a test suite in `tests.py`. It needs neither Domoticz nor network access: the Domoticz plugin API is replaced by an in-memory fake and all HTTP calls are mocked. Besides the plugin behaviour, it checks that the API key and your location never reach the log, that devices 1–10 are unchanged, and that the log messages quoted in these docs still exist in `plugin.py`.

```bash
python3 tests.py
```

For a basic syntax check:

```bash
python3 -m py_compile plugin.py tests.py
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contribution workflow.

## Release checklist

Before publishing a release:

```bash
python3 tests.py
git status --short --ignored
git add .
git commit -m "Release <VERSION>"
git tag -a <VERSION> -m "Release <VERSION>"
```

Then push the actual branch and tag:

```bash
git push origin <branch>
git push origin <VERSION>
```

Check the branch with `git branch --show-current` instead of assuming `master` or `main`. Update the version in `plugin.py`, `README.md`, `CHANGELOG.md`, `SECURITY.md` and `ROADMAP.md` together; `tests.py` checks that they agree. Release tags are plain numeric, for example `v4.1` (see [`CHANGELOG.md`](CHANGELOG.md)).

## Documentation

| File | Contents |
|---|---|
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |
| [`ROADMAP.md`](ROADMAP.md) | Planned and potential work |
| [`DEPLOY.md`](DEPLOY.md) | Installation, update, rollback and troubleshooting |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to report problems and contribute |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting and secret handling |
| [`.github/CODE_OF_CONDUCT.md`](.github/CODE_OF_CONDUCT.md) | Community standards |

## Security

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting and API-key handling.

## License

MIT License. See [`LICENSE`](LICENSE).

## Data and credits

Air-quality data is provided by [OpenAQ](https://openaq.org) and its data providers; check the OpenAQ terms and the licence of each data source before reusing the data. Plugin originally created by Xorfor, extended by waltervl, and maintained by Jan Reimen (janreimen).

OpenAQ API documentation: <https://docs.openaq.org>
