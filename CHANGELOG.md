# Changelog

All notable changes to **Domoticz-OpenAQ-Plugin** are documented in this file.

The project follows [Semantic Versioning](https://semver.org/) where practical.

* **Tag format:** release tags are plain numeric (`MAJOR.MINOR` or `MAJOR.MINOR.PATCH`, optionally `v`-prefixed, for example `v4.1`) with no `-alpha`/`-beta` suffixes. This is the same convention as Domoticz-MySkodaAPI and is compatible with PyPluginStore's release-channel tag matcher (`^v?[0-9]+(?:\.[0-9]+){1,3}$`). Pre-release iteration happens as unreleased commits on the `development` branch.

---

# Release History

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
