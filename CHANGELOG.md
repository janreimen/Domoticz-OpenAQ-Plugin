# Changelog

All notable changes to **Domoticz-OpenAQ-Plugin** are documented in this file.

The project follows [Semantic Versioning](https://semver.org/) where practical.

* **Tag format:** release tags are plain numeric (`MAJOR.MINOR` or `MAJOR.MINOR.PATCH`, optionally `v`-prefixed, for example `v4.0`) with no `-alpha`/`-beta` suffixes. This is the same convention as Domoticz-MySkodaAPI and is compatible with PyPluginStore's release-channel tag matcher (`^v?[0-9]+(?:\.[0-9]+){1,3}$`). Pre-release iteration happens as unreleased commits on the `development` branch.

---

# Release History

## [Unreleased]

Documentation, repository standards and tests only. **No change to `plugin.py`**, so the plugin behaves exactly as in 4.0.

### Added

* `CHANGELOG.md`, `ROADMAP.md`, `DEPLOY.md` and `CONTRIBUTING.md`.
* `.github/` community files: `CODE_OF_CONDUCT.md`, `PULL_REQUEST_TEMPLATE.md`, and GitHub issue forms for bug reports and feature requests (`.yml`), with a `config.yml` that disables blank issues.
* `requirements.txt` stating that no third-party dependencies are required.
* `tests.py`: a standard-library test suite that needs neither Domoticz nor network access. It covers the plugin manifest, startup and location discovery, device creation, reading parsing, the air-quality level calculation, request construction, hourly polling, the startup HTTPS helper (including certificate verification), and repository standards.

### Changed

* `README.md` rewritten to describe the 4.0 behaviour. The previous text still described scanning all stations in a radius, omitted the **Pollutants** alert device (unit 9), omitted the **Debug** setting, and described the Info device as it was in older versions. It now also documents installation, device units, how readings are processed and known limitations.
* `SECURITY.md` rewritten in the project's standard layout and made specific to this plugin, including how the API key is handled and which logs to redact.
* `.gitignore` extended (secrets, local configuration, editor and working files).
* `LICENSE`: added the maintainer's copyright line next to the original Xorfor notice, which is retained as the MIT licence requires.

### Documented known issues

The following were confirmed while writing the tests and are listed in `ROADMAP.md` as planned fixes. None are fixed in this entry.

* With Debug enabled, the API key and the Domoticz location are written to the log.
* The API-key field in the Domoticz hardware form is not masked.
* A failed start (for example a missing API key) is followed by an `AttributeError` on every heartbeat.
* A start with an empty Domoticz location raises an `IndexError`, even when a Location ID is configured.
* A radius above OpenAQ's 25 km maximum is passed on unchanged.

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
