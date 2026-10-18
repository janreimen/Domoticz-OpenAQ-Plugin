# Roadmap

The roadmap describes the intended future development of **Domoticz-OpenAQ-Plugin**.

The project is currently at **4.2 — Released**.

The roadmap deliberately separates:

* **Known defects** — behaviour confirmed to be wrong.
* **Planned** — intended development target.
* **Potential** — useful future functionality, but not yet committed.
* **API-dependent** — dependent on capabilities exposed by the OpenAQ API.
* **Deferred** — deliberately postponed.

Features are **not considered implemented** until they are released in a version of the plugin.

---

# Current State — 4.2

Multi-station release. One hardware instance can read up to 20 OpenAQ stations: a comma-separated list of Location IDs, or an automatic lookup of the nearest *Max auto-detected stations* (default 3), ranked by distance inside the plugin. Every station owns a block of ten devices (the first station keeps units 1–10), tagged with its location ID so that it always finds its own devices again, whatever the order of the list, a temporary failure or a removed ID. Devices of 4.0 and 4.1 are recognised and kept. A station that cannot be set up is skipped and reported, and the startup lookups stop as soon as it is clear that every request will fail. See `CHANGELOG.md`.

Since 4.1: the API-key field is masked and neither the key nor your Domoticz location can reach the log; a failed start leaves the plugin idle with a clear message. Since 4.0: OpenAQ API v3, seven pollutant sensors, an Air Quality Index, a Pollutants alert and an Info device. Read-only.

---

# Support policy

| Version | Supported |
|---|---|
| 4.2.x | Yes. Current release. |
| 4.1.x | Yes. |
| 4.0 and earlier | No. |

**Support for versions before 4.2 will end in a future release.** From then on only 4.2 and later are supported, and new code may stop being tested against upgrades from earlier versions. The date is not fixed; it will be announced in `CHANGELOG.md`. Moving from 4.1 to 4.2 needs no other action than updating the plugin; going back to 4.1 is described in `DEPLOY.md`.

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
* **A combined level across stations**, for example the worst Air Quality Index of all stations as one extra device.
* **Rank more than the first 100 locations** of the automatic lookup by paging through OpenAQ's results, for very dense areas.
* **Fewer startup requests**: use the sensors that the location search may already return, instead of one more lookup per station (to be confirmed against the live API).
* **GitHub Actions** workflow running `tests.py` on every push and pull request.
* **PyPluginStore** listing, with a `registry.json` entry in the PyPluginStore repository.

---

# API-dependent

* Additional parameters that OpenAQ may report at a location (the plugin currently ignores everything except BC, CO, NO<sub>2</sub>, O<sub>3</sub>, PM<sub>10</sub>, PM<sub>2.5</sub> and SO<sub>2</sub>).
* Distance-ordered location search, if OpenAQ adds an `order_by` value for it. The plugin ranks the results itself until then.
* A single request for the latest values of several locations, if OpenAQ offers one. Today every station costs one request per poll.

---

# Deferred

Nothing is currently deferred.
