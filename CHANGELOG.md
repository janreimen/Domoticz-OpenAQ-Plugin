# Changelog

All notable changes to **Domoticz-OpenAQ-Plugin** are documented in this file.

The project follows [Semantic Versioning](https://semver.org/) where practical.

* **Tag format:** release tags are plain numeric (`MAJOR.MINOR` or `MAJOR.MINOR.PATCH`, optionally `v`-prefixed, for example `4.2`) with no `-alpha`/`-beta` suffixes. This is the same convention as Domoticz-MySkodaAPI and is compatible with PyPluginStore's release-channel tag matcher (`^v?[0-9]+(?:\.[0-9]+){1,3}$`). Pre-release iteration happens as unreleased commits on the `development` branch.

---

# Release History

## [4.2] - 2026-10-17

Multi-station support. One hardware instance can now read several OpenAQ stations ("stations" are what OpenAQ calls locations). **Nothing has to be re-created when upgrading from 4.1 or 4.0**: the plugin key (`xfr_openaq4`) is unchanged, the first station keeps device units 1–10, and an existing single Location ID is simply a list of one.

### Added

* **Several stations per hardware instance (up to 20).**
  * **OpenAQ Location IDs** (hardware field `Mode3`, previously *OpenAQ Location ID*) takes a comma-separated list, for example `9645,1140`. Spaces are ignored. An entry that is not a positive whole number is ignored and reported, a duplicate is used once, and entries after the 20th are ignored and reported.
  * **Max auto-detected stations** (new hardware field `Mode4`, default `3`, at most 20) says how many stations the automatic lookup uses when the Location IDs are blank. It is ignored when IDs are entered.
* **A block of ten units per station.** The first station uses units 1–10 as before, the second 11–20, and so on up to units 191–200. Every station has its own connection, readings, Info, Pollutants alert and Air Quality Index device, so the values of different stations never mix. The Air Quality Index of each station is unchanged; there is no combined level.
* **A station always finds its own devices again.** New devices are tagged with their location ID (the Domoticz *DeviceID* `OAQ<id>-<unit in block>`). At startup every station gets the block that carries its ID, and the lowest free block when it has none. Reordering the list, removing an ID, adding one in front, or a temporary failure of one station therefore cannot move a station onto the devices of another one, which would have mixed their history. The block of a station that is no longer used stays reserved for it, with its devices and history untouched, and the log says so at every start; adding the ID again continues the history. With all 20 blocks in use, a new station is skipped and reported.
* **Devices created by 4.0 and 4.1 are recognised.** They carry no tag, so the plugin identifies their station from the **Info** device (`OpenAQ Location ID: <id>`, which every poll writes) and keeps them with that station. Devices that were never updated go to the first station without a block.
* **Device names say which station they belong to** when several stations are configured (`PM2.5 (Beidweiler)`, with ` #<id>` added when two stations share a name). With one station the names are exactly those of 4.0 and 4.1. Existing devices are never renamed. Station names come from OpenAQ and are stripped of markup and control characters and shortened to 40 characters.
* **The automatic lookup ranks by distance itself.** OpenAQ documents no distance ordering for `GET /v3/locations`, so the plugin requests a full page (100 locations) and ranks them by the coordinates OpenAQ returns for each, falling back to OpenAQ's `distance` field (never a mix of the two), and finally to OpenAQ's order, which the log then says is not necessarily the nearest. A full page is reported, because there may be more locations in the radius. Coordinates and distances are never logged.
* **Stations that already have devices are kept first** by the automatic lookup, so history carries on when a nearer station appears, and the log says `, kept because it already has devices`. The log ends with the exact list to copy into the Location IDs field: `To keep using the auto-detected location(s), enter <id1>,<id2>,<id3> in the 'OpenAQ Location IDs' hardware field.`
* **A station that cannot be set up is skipped, not fatal.** The plugin starts if at least one station works, and logs `OpenAQ started with <k> of <n> stations. Not configured: <ids>.` The lookups stop as soon as a request shows that all further ones would fail too (OpenAQ unreachable, API key rejected with 401/403, rate limit 429), instead of waiting for one 10 s timeout per station: `Skipping the remaining <n> OpenAQ location(s): <reason>.`
* **Supported versions.** 4.2 and 4.1 are supported; support for versions before 4.2 will end in a future release (see `README.md`, `SECURITY.md` and `ROADMAP.md`).

### Changed

* **Upgrading an automatic-lookup instance adds no devices.** An empty *Max auto-detected stations* field, which is what an instance created by 4.1 has until its hardware page is saved, means `1`, which is what 4.1 did; the plugin keeps the station it already uses even if a nearer one exists. The form offers `3` for new hardware. A value that is not a positive whole number uses `3`; a value above 20 uses 20.
* The automatic lookup requests 100 locations (4.1: 1) and ranks them. The log lines changed accordingly (`Looking for up to <n> OpenAQ location(s) within <r> km ...`, `OpenAQ returned <n> location(s); the nearest ones are used.`, `Auto-detected OpenAQ location <i> of <n>: '<name>' (id <id>)`), replacing `Auto-selected OpenAQ location ...` and `To keep using this location ...` of 4.1.
* Connections are named `openaq_<id>` (4.1: `openaq`), so the status messages read `openaq_<id> returned a status: <status>`.
* Hardware labels: `OpenAQ Location IDs, comma separated (blank = auto-detect)`, `Max auto-detected stations` (new) and `Radius (km, max 25, used only if Location IDs is blank)`. The field positions and stored values are unchanged.
* `OpenAQ Location ID must be numeric` and `... must be a positive number` are replaced by `OpenAQ Location ID '<entry>' is not a positive whole number and is ignored` and, when nothing usable is left, `No valid OpenAQ Location ID entered`. An invalid entry in a list no longer stops the other stations.
* The Debug dump shows the new `Mode4` value (the number of stations is harmless).
* Version 4.2.

### Fixed

* **A key pasted into the wrong field was written to the log.** 4.1 repeated the text of the Location ID and radius fields in its messages. Entries are now scrubbed of the API key and shortened to 30 characters before they are repeated.
* **A malformed answer from OpenAQ raised an exception inside `onStart`** (reproduced against 4.1: a JSON list instead of an object gave `AttributeError`, `results` holding non-objects gave `AttributeError`, a sensor without an `id` gave `KeyError`, `sensors: null` gave `TypeError`). These are now reported as a failed lookup of that station.

### Documentation and repository

* `README.md`, `DEPLOY.md`, `SECURITY.md`, `ROADMAP.md` and `CONTRIBUTING.md` describe the station blocks, how stations find their devices, the automatic lookup, the upgrade from 4.1, the way back to 4.1, the new log messages and the support policy. The issue and pull-request templates mention the new rules.
* `tests.py` grew from 85 to over 170 tests. New: location-ID list parsing, device blocks and tags, reordering, removal, temporary failure, reserved and exhausted slots, recognition of 4.0/4.1 devices (from the Info device, and when tags are missing), the ranking of the automatic lookup (coordinates, distance field, OpenAQ order, malformed values, sticky stations), the *Max auto-detected stations* rules, device naming, partial failure and the fail-fast stop, the upgrade from 4.1, log hygiene for several stations, and that the documented hardware labels, version and support policy match the code.

---

## [4.1] - 2026-10-16

Robustness and secret handling. **Device units 1–10 are unchanged**, so an upgrade from 4.0 keeps all devices and their history; nothing has to be re-created. The plugin key (`xfr_openaq4`) is unchanged as well. Every item below was reproduced against the real 4.0 `plugin.py` before it was changed.

### Security

* **The API-key field is masked** in the Domoticz hardware form (`password="true"`). The stored value is kept; the field just shows dots from now on.
* **The API key is no longer written to the log.** In 4.0, `config_2_log()` printed `Parameter 'Mode2'...: '<your key>'` at Debug level. It now prints a value only for an allow-list of harmless names (the radius, the location ID, the Debug setting and Domoticz's own folder and version entries); every other value is shown as `<masked>`, or `<empty>` when blank. Names that are not on the list, including any Domoticz adds in future, are masked by default.
* **Your Domoticz location is no longer written to the log.** 4.0 printed it in the same Debug dump (`Setting 'Location'...`).
* **Coordinates no longer leak through request logging.** In 4.0 the automatic lookup put your coordinates in the request path, which was logged at Debug level for every lookup, and at **Error** level (visible with Debug off) when the lookup failed or found nothing, including the line `No OpenAQ location found within 10000 m of <lat>,<lon>`. Request paths are now logged without their query string (`/v3/locations?<query hidden>`), and the error no longer prints coordinates.
* **The API key is scrubbed from logged response text and errors** (HTTP error bodies, exception messages, and the Debug line that dumps each poll response), as defence in depth.
* **If you ever shared a Debug log from 4.0 or earlier, rotate your API key** at <https://explore.openaq.org/account> and enter the new key in Domoticz.

### Fixed

* **Heartbeat errors after a failed start.** When `onStart` stopped early (missing API key, OpenAQ unreachable, location without sensors, …) the connection object was never created, and every following heartbeat raised `AttributeError: 'NoneType' object has no attribute 'Connecting'`, hiding the real error. `onConnect`, `onMessage` and `onHeartbeat` now do nothing unless `onStart` completed, and a failed start ends with one clear message: `OpenAQ plugin not started - see the message above. It stays idle until the hardware is restarted or updated.`
* **Start failed with an empty Domoticz location even when a Location ID was entered** (`IndexError: list index out of range`). The Domoticz location is now read only for the automatic lookup. With a Location ID it is not read at all. For the automatic lookup, an unset or invalid location (not two numbers, or outside ±90 / ±180) now gives a clear error instead of an exception, and the coordinates are rounded to the four decimal places OpenAQ uses.
* **Radius handling.**
  * A radius above OpenAQ's documented maximum of 25 km was sent unchanged. It is now reduced to 25 km and logged.
  * A decimal radius such as `2.5` fell back to the default of 10 km because it was parsed as an integer. Decimals are accepted now, including a decimal comma (`2,5`).
  * Empty, non-numeric, `nan`, `inf` and non-positive values use the default of 10 km, and the log says so.
  * The radius actually used is always logged: `Looking for an OpenAQ location within <n> km of the Domoticz location`.
* **The auto-detect log message claimed to have found the *nearest* location**, but OpenAQ's API documentation does not say that results are ordered by distance. It now reads `Auto-selected OpenAQ location '<name>' (id <id>): the first location OpenAQ returned within <n> km, which is not necessarily the nearest one.`, followed by `To keep using this location, enter <id> in the 'OpenAQ Location ID' hardware field.`
* **A zero or negative Location ID was accepted** and sent to OpenAQ (`/v3/locations/-5`). It is now rejected with `OpenAQ Location ID must be a positive number`.
* **Whitespace around the API key was sent to OpenAQ.** The key is now trimmed.

### Changed

* The hardware field labels now read `Radius (km, max 25, used only if Location ID is blank)` and `OpenAQ Location ID (blank = auto-detect)`. The field positions (`Mode1`, `Mode2`, `Mode3`, `Mode6`) and their stored values are unchanged.
* `onStart` now returns `True` on success (Domoticz ignores the value).
* Version 4.1.

### Documentation and repository

* Added `CHANGELOG.md`, `ROADMAP.md`, `DEPLOY.md` and `CONTRIBUTING.md`.
* Added `.github/` community files: `CODE_OF_CONDUCT.md`, `PULL_REQUEST_TEMPLATE.md`, and GitHub issue forms for bug reports and feature requests (`.yml`), with a `config.yml` that disables blank issues.
* Added `requirements.txt` stating that no third-party dependencies are required.
* Added `tests.py`, a standard-library test suite that needs neither Domoticz nor network access. It covers the plugin manifest, startup and location discovery, device creation, reading parsing, the air-quality level calculation, request construction, hourly polling and the startup HTTPS helper (including certificate verification). New for 4.1: it checks that neither the API key nor the location reaches any log line (with the HTTPS layer mocked, so the real logging is exercised), that a failed start leaves the plugin idle, that the location is only read when needed, the radius rules, that devices 1–10 match their 4.0 definitions and that 4.0-created devices are reused, and that every log message quoted in the troubleshooting tables still exists in `plugin.py`.
* `README.md` rewritten to describe the current behaviour (the previous text still described scanning all stations in a radius, and omitted the Pollutants alert device and the Debug setting).
* `SECURITY.md` rewritten in the project's standard layout and specific to this plugin.
* `.gitignore` extended (secrets, local configuration, editor and working files).
* `LICENSE`: added the maintainer's copyright line next to the original Xorfor notice, which is retained as the MIT licence requires.

---

## [4.0] - 2026-08-18

Migration to **OpenAQ API v3**. OpenAQ retired the v1/v2 endpoints on 2025-01-31; they now return HTTP 410 Gone, so earlier plugin versions no longer work.

### Changed

* Readings come from `GET /v3/locations/{id}/latest`. This endpoint returns one row per sensor, identified only by `sensorsId`, with no parameter name or unit. The plugin therefore makes a one-off call to `GET /v3/locations/{id}` at startup to build a `sensorsId` to (parameter, unit) map before polling.
* The plugin now targets a **single OpenAQ location** instead of scanning every location inside a radius on every poll.
* **Location ID** (hardware field `Mode3`) selects that location. If it is blank, the plugin makes a best-effort lookup of one location once at startup, using the Domoticz coordinates and the configured radius (`GET /v3/locations?coordinates=<lat>,<lon>&radius=<m>&limit=1`).
* The **Radius** field is now only used for that startup lookup.
* HTTP 410 responses are reported with an explicit message asking you to check for a plugin update.
* The plugin is registered as **OpenAQ 4** with key `xfr_openaq4`, version `4.0`.

---

## [3.1] and earlier

These releases predate the history of this repository, which starts at 4.0. The plugin was originally written by Xorfor (MIT licence, 2018) and later extended by waltervl. From v3.1 an OpenAQ API key is required.
