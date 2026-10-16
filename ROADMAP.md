# Roadmap

The roadmap describes the intended future development of **Domoticz-OpenAQ-Plugin**.

The project is currently at **4.0 — Released**.

The roadmap deliberately separates:

* **Known defects** — behaviour confirmed to be wrong; fixes are planned.
* **Planned** — intended development target.
* **Potential** — useful future functionality, but not yet committed.
* **API-dependent** — dependent on capabilities exposed by the OpenAQ API.
* **Deferred** — deliberately postponed.

Features are **not considered implemented** until they are released in a version of the plugin.

---

# Current State — 4.0

Migrated to OpenAQ API v3 after OpenAQ retired v1/v2 (HTTP 410). One OpenAQ location per hardware instance, selected by Location ID or by a one-time lookup near the Domoticz coordinates. Seven pollutant sensors, an Air Quality Index, a Pollutants alert and an Info device. Read-only. See `CHANGELOG.md`.

---

# Known defects

Each item below was reproduced against the real `plugin.py` with the fake Domoticz API from `tests.py`. Fixes are planned, not yet implemented.

* **API key and location in the Debug log.** `config_2_log()` writes every hardware parameter and every Domoticz setting at Debug level. That includes the API key (`Mode2`) and the Domoticz location. See `SECURITY.md`.
* **API key field is not masked.** The `OpenAQ API-KEY` field in `plugin.py`'s manifest lacks `password="true"`, so Domoticz shows the key in clear text.
* **Heartbeat crash after a failed start.** If `onStart` returns early (for example a missing API key, an unreachable OpenAQ, or a location without sensors), the connection object is never created, and every subsequent `onHeartbeat` raises `AttributeError: 'NoneType' object has no attribute 'Connecting'`. The original startup error is easy to miss in the resulting log noise.
* **Empty Domoticz location.** `onStart` splits `Settings["Location"]` unconditionally, so an unset location raises `IndexError` even when a Location ID is configured and the coordinates are not needed.
* **Radius above 25 km.** The radius is multiplied by 1000 and sent as given; OpenAQ documents a maximum of 25,000 m for this search.
* **Auto-detect log message.** The startup log says "Auto-selected nearest OpenAQ location", but OpenAQ's documentation does not say that `GET /v3/locations` results are ordered by distance (the only documented `order_by` value is `id`), so the location chosen is the first one returned, not necessarily the nearest.

---

# Planned

## 4.1 — Robustness and secret handling

* Mask the API-key field (`password="true"`).
* Mask the API key, and the Domoticz location, in `config_2_log()`.
* Fail safely after a failed start: do nothing in `onHeartbeat`, `onMessage` and `onConnect` unless `onStart` completed.
* Read the Domoticz coordinates only when the automatic lookup needs them.
* Validate the radius against OpenAQ's 25 km maximum and log what was used.
* Word the auto-detect log message accurately, and recommend copying the chosen ID into the Location ID field.
* Keep device unit numbers 1–10 unchanged so existing devices and history survive the upgrade.

---

# Potential

* **Pollutants text** listing every pollutant at the highest level, instead of the pollutants that raised the running maximum during evaluation.
* **Unit handling**: convert ppm/ppb readings to µg/m³ before comparing them with the limits, or skip the level calculation for pollutants whose unit is not understood.
* **Zero readings**: accept `0` while still rejecting the `-999` sentinel (the current `> 0` test also drops genuine zeros).
* **Staleness**: mark a pollutant device as timed out when its newest reading is older than a configurable age.
* **Last measurement time** as a text device.
* **Configurable poll interval** (the source notes "1 hour or use a parameter").
* **Nearest-location choice** that does not depend on result ordering, for example by requesting several locations and choosing by distance in the plugin.
* **Several locations** per hardware instance, as earlier versions scanned every location inside the radius.
* **GitHub Actions** workflow running `tests.py` on every push and pull request.
* **PyPluginStore** listing, with a `registry.json` entry in the PyPluginStore repository.

---

# API-dependent

* Additional parameters that OpenAQ may report at a location (the plugin currently ignores everything except BC, CO, NO<sub>2</sub>, O<sub>3</sub>, PM<sub>10</sub>, PM<sub>2.5</sub> and SO<sub>2</sub>).
* Distance-ordered location search, if OpenAQ adds an `order_by` value for it.

---

# Deferred

Nothing is currently deferred.
