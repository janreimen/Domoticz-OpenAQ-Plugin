# Roadmap

The roadmap describes the intended future development of **Domoticz-OpenAQ-Plugin**.

The project is currently at **4.1 — Released**.

The roadmap deliberately separates:

* **Known defects** — behaviour confirmed to be wrong.
* **Planned** — intended development target.
* **Potential** — useful future functionality, but not yet committed.
* **API-dependent** — dependent on capabilities exposed by the OpenAQ API.
* **Deferred** — deliberately postponed.

Features are **not considered implemented** until they are released in a version of the plugin.

---

# Current State — 4.1

Robustness and secret handling release. The API-key field is masked and neither the key nor your Domoticz location can reach the log. A failed start leaves the plugin idle with a clear message. The Domoticz location is only needed for the automatic lookup, the radius is validated against OpenAQ's 25 km maximum, and the auto-detect message no longer claims the nearest location. Devices 1–10 are unchanged, so upgrades keep existing devices and history. See `CHANGELOG.md`.

Under the hood since 4.0: OpenAQ API v3, one OpenAQ location per hardware instance, seven pollutant sensors, an Air Quality Index, a Pollutants alert and an Info device. Read-only.

---

# Known defects

None currently open. Please report problems with the GitHub issue template.

The behaviour listed under "Known limitations" in `README.md` is understood and documented; the items that could be improved are listed below under Potential.

---

# Planned

Nothing is currently scheduled. The items under Potential are the candidates for the next release.

---

# Potential

* **Retry the startup lookups from the heartbeat.** Today a failed start (for example when Domoticz boots before the network is up, or OpenAQ is briefly unreachable) leaves the plugin idle until the hardware is restarted or updated. A retry with back-off would make that self-healing.
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
