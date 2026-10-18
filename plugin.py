#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  OpenAQ Python Plugin
#
# Author: Xorfor
# Maintainer: Waltervl, janreimen
#
# v4.2 - Multi-station support. Existing hardware keeps working unchanged.
#
#        * "OpenAQ Location IDs" takes a comma-separated list, for example
#          "9645,1140". A single ID works exactly as before.
#        * Leave it blank to auto-detect up to "Max auto-detected stations"
#          (default 3) locations around your Domoticz location. They are ranked
#          by distance here, because OpenAQ does not document its result order.
#          Stations that already have devices are kept first, so devices keep
#          their history when the neighbourhood changes.
#        * Every station owns a block of 10 Domoticz units: the first station
#          uses units 1-10 (as in 4.0 and 4.1), the next 11-20, and so on, up to
#          20 stations. Devices are tagged with their OpenAQ location ID
#          (DeviceID "OAQ<id>-<n>"), and units 1-10 created by 4.0/4.1 are
#          recognised through their Info device. A station therefore always
#          finds its own devices again, whatever the order of the list, a
#          temporary failure or a removed ID.
#        * Every station has its own connection and its own readings, so the
#          values of different stations never mix.
#        * A station that cannot be set up is skipped and reported; the others
#          run. Lookups stop at the first sign that every request will fail
#          (OpenAQ unreachable, key rejected, rate limit).
#
# v4.1 - Robustness and secret handling. Device units 1-10 are unchanged, so
#        existing devices and their history are kept when upgrading.
#
#        * The API-key field is masked in the Domoticz hardware form.
#        * The API key and the Domoticz location are never written to the
#          log: config_2_log() shows values only for an allow-list of harmless
#          names, request paths are logged without their query string (the
#          automatic lookup carries your coordinates there), and the key is
#          scrubbed from any logged response text or error.
#        * After a failed start the plugin stays idle: onConnect, onMessage
#          and onHeartbeat do nothing unless onStart completed.
#        * The Domoticz location is only read, and only has to be valid, for
#          the automatic location lookup.
#        * The radius is validated (decimals and a decimal comma are accepted,
#          the maximum is the 25 km OpenAQ documents) and the value used is
#          logged.
#        * The auto-detect log message no longer claims the location is the
#          nearest one, and tells you which ID to enter to keep it.
#
# v4.0 - Migrated to OpenAQ API v3.
#        OpenAQ retired the v1/v2 endpoints on 2025-01-31; they now return
#        HTTP 410 Gone. The v3 "/latest" endpoint returns one row per sensor
#        (identified only by "sensorsId"), with no inline parameter name or
#        unit, so at startup we do a one-off call to GET /v3/locations/{id}
#        to build a sensorsId -> (parameter, unit) map before polling.
#
#        The plugin now targets a single OpenAQ Location ID rather than
#        scanning every location inside a radius on every poll. If you leave
#        the Location ID field blank, the plugin does a best-effort lookup
#        of a location near you once at startup, using your Domoticz
#        Settings coordinates and the configured radius.
#
# Air Quality Index based on:
#   http://www.airqualitynow.eu/about_indices_definition.php

"""
<plugin key="xfr_openaq4" name="OpenAQ 4" author="Xorfor, Waltervl, janreimen" version="4.2" wikilink="https://github.com/janreimen/Domoticz-OpenAQ-Plugin" externallink="https://openaq.org/">
    <params>
        <param field="Mode1" label="Radius (km, max 25, used only if Location IDs is blank)" width="75px" default="10" required="false"/>
        <param field="Mode2" label="OpenAQ API-KEY" width="200px" default="" required="true" password="true"/>
        <param field="Mode3" label="OpenAQ Location IDs, comma separated (blank = auto-detect)" width="250px" default="" required="false"/>
        <param field="Mode4" label="Max auto-detected stations" width="75px" default="3" required="false"/>
        <param field="Mode6" label="Debug" width="75px">
            <options>
                <option label="True" value="Debug"/>
                <option label="False" value="Normal" default="true"/>
            </options>
        </param>
    </params>
</plugin>
"""
import Domoticz
import json
import math
import re
import ssl
import http.client
import urllib.parse
from datetime import datetime
import time

# Radius of the automatic location lookup. OpenAQ documents a maximum of
# 25,000 m for the "radius" parameter of GET /v3/locations.
DEFAULT_RADIUS_KM = 10
MAX_RADIUS_KM = 25

# Stations. Every station owns UNITS_PER_STATION consecutive Domoticz units:
# station block 0 is units 1-10 (the units of 4.0 and 4.1), block 1 is 11-20,
# and so on. 20 blocks stay below Domoticz's limit of 255 units.
UNITS_PER_STATION = 10
MAX_STATIONS = 20
DEFAULT_MAX_AUTO_STATIONS = 3
# GET /v3/locations returns 100 results by default. The automatic lookup asks
# for that many and ranks them by distance itself.
DISCOVERY_PAGE_SIZE = 100

# Local unit (1-10) of every device inside a station block. These numbers are
# the units of 4.0 and 4.1 and must never change.
INFO_UNIT = 8
ALERT_UNIT = 9
AQI_UNIT = 10
POLLUTANTS = {
    # key: (local unit, name, (low, medium, high, very high) limits)
    "bc": (1, "BC", (None, None, None, None)),
    "co": (2, "CO", (5000, 7500, 10000, 20000)),
    "no2": (3, "NO<sub>2</sub>", (50, 100, 200, 400)),
    "o3": (4, "O<sub>3</sub>", (60, 120, 180, 240)),
    "pm10": (5, "PM<sub>10</sub>", (25, 50, 90, 180)),
    "pm25": (6, "PM<sub>2.5</sub>", (15, 30, 55, 110)),
    "so2": (7, "SO<sub>2</sub>", (50, 100, 350, 500)),
}

# New devices are tagged with the OpenAQ location they belong to, so that a
# station finds its devices again whatever the order of the Location IDs. The
# Info device ("OpenAQ Location ID: 9645<br/>...") identifies the station of
# devices that were created without a tag (units 1-10 of 4.0 and 4.1).
DEVICE_ID_PREFIX = "OAQ"
DEVICE_ID_RE = re.compile(r"OAQ([0-9]+)-([0-9]{1,2})")
INFO_TEXT_RE = re.compile(r"OpenAQ Location ID:\s*([0-9]+)")
LOCATION_ID_RE = re.compile(r"[0-9]{1,12}")
MAX_NAME_LENGTH = 40

# config_2_log() prints a value only for the names listed here; every other
# value (the API key, credentials, the Domoticz location, anything Domoticz
# adds in the future) is replaced by MASKED, or EMPTY when it is blank.
MASKED = "<masked>"
EMPTY = "<empty>"
LOGGABLE_PARAMETERS = frozenset(
    [
        "HardwareID",
        "HardwareName",
        "Name",
        "Key",
        "Author",
        "Version",
        "Language",
        "HomeFolder",
        "StartupFolder",
        "UserDataFolder",
        "Database",
        "WebRoot",
        "DomoticzVersion",
        "DomoticzHash",
        "DomoticzBuildTime",
        "Mode1",  # radius
        "Mode3",  # location IDs
        "Mode4",  # max auto-detected stations
        "Mode6",  # debug
    ]
)
LOGGABLE_SETTINGS = frozenset(["Language"])


class Station:
    """One OpenAQ location: its device block, sensor map, readings and connection."""

    def __init__(self, location_id, block):
        self.id = location_id
        self.block = block  # 0-based; units block*10+1 ... block*10+10
        self.name = "Location {}".format(location_id)
        self.label = self.name  # text added to device names when there are several stations
        self.sensor_map = {}  # sensorsId (int) -> {"parameter": "pm25", "units": "µg/m³"}
        # key -> [date, value, unit]; the newest valid reading of every pollutant
        self.values = {key: [None, None, None] for key in POLLUTANTS}
        self.conn_name = "openaq_{}".format(location_id)
        self.url = "/v3/locations/{}/latest".format(location_id)
        self.conn = None

    def unit(self, local_unit):
        return self.block * UNITS_PER_STATION + local_unit

    def device_id(self, local_unit):
        return "{}{}-{}".format(DEVICE_ID_PREFIX, self.id, local_unit)

    def forget_readings(self):
        for key in self.values:
            self.values[key] = [None, None, None]


class BasePlugin:

    __HEARTBEATS2MIN = 6
    __MINUTES = 60  # 1 hour or use a parameter

    __API_ENDPOINT = "api.openaq.org"

    __LEVELS = {0: "Very low", 1: "Low", 2: "Medium", 3: "High", 4: "Very high"}

    def __init__(self):
        self.__API_KEY = ""
        self.__reset()

    def __reset(self):
        # State of a (re)start. Everything here is rebuilt by onStart.
        self.__started = False  # True only once onStart has completed
        self.__runAgain = 0
        self.__stations = {}  # location ID -> Station, in the order of the list
        self.__by_connection = {}  # connection name -> Station
        self.__multiple = False  # more than one station: device names carry the station
        self.__stop_lookups = None  # reason why the remaining startup lookups are skipped

    # ------------------------------------------------------------------
    # Logging helpers. Nothing that reaches the log may contain the API
    # key or the Domoticz location.
    # ------------------------------------------------------------------
    def __scrub(self, text):
        # Defence in depth: remove the API key from any text before logging
        # it (response bodies, exception messages).
        text = str(text)
        if self.__API_KEY:
            text = text.replace(self.__API_KEY, "<api-key>")
        return text

    def __echo(self, value):
        # A value the user typed, shown back in a message: scrubbed and short.
        text = self.__scrub(value)
        return text if len(text) <= 30 else text[:30] + "..."

    @staticmethod
    def __loggable_path(path):
        # The automatic lookup puts the Domoticz coordinates in the query
        # string, so only the path is ever logged.
        base, separator, _query = path.partition("?")
        return base + ("?<query hidden>" if separator else "")

    # ------------------------------------------------------------------
    # One-off blocking HTTPS helper, used only at onStart for the setup
    # lookups below (one location search and one lookup per station).
    # Domoticz's async Connection objects (used for the recurring heartbeat
    # poll further down) are the normal pattern for anything repeated, but a
    # few short blocking calls at startup are a widely used, accepted
    # simplification for one-time metadata lookups.
    #
    # When a request shows that every further request will fail as well
    # (OpenAQ unreachable, key rejected, rate limit), the remaining startup
    # lookups are skipped instead of waiting for one timeout after another.
    # ------------------------------------------------------------------
    def __blocking_get(self, path):
        shown = self.__loggable_path(path)
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(self.__API_ENDPOINT, 443, timeout=10, context=ctx)
        try:
            try:
                headers = {
                    "Host": self.__API_ENDPOINT,
                    "User-Agent": "Domoticz/1.0",
                    "X-API-Key": self.__API_KEY,
                    "Accept": "application/json",
                }
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                body = resp.read()
            except Exception as e:
                self.__stop_lookups = "OpenAQ could not be reached"
                Domoticz.Error(
                    "OpenAQ request {} failed: {}".format(shown, self.__scrub(e))
                )
                return None
            Domoticz.Debug("Blocking GET {} -> HTTP {}".format(shown, status))
            if status != 200:
                if status in (401, 403):
                    self.__stop_lookups = "OpenAQ rejected the API key (HTTP {})".format(status)
                elif status == 429:
                    self.__stop_lookups = "the OpenAQ rate limit was reached"
                Domoticz.Error(
                    "OpenAQ request {} returned HTTP {}: {}".format(
                        shown, status, self.__scrub(body[:200].decode("utf-8", "ignore"))
                    )
                )
                return None
            try:
                return json.loads(body.decode("utf-8", "ignore"))
            except ValueError as e:
                Domoticz.Error(
                    "OpenAQ request {} failed: {}".format(shown, self.__scrub(e))
                )
                return None
        finally:
            conn.close()

    def __domoticz_coordinates(self):
        # Only called when the automatic lookup needs them. The values are
        # never logged.
        raw = Settings.get("Location") or ""
        parts = [part.strip() for part in str(raw).split(";")]
        lat = lon = None
        if len(parts) == 2:
            try:
                lat, lon = float(parts[0]), float(parts[1])
            except ValueError:
                lat = lon = None
        if (
            lat is None
            or not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90.0 <= lat <= 90.0
            or not -180.0 <= lon <= 180.0
        ):
            Domoticz.Error(
                "The Domoticz location is not set or not valid "
                "(Setup > Settings > System > Location). Set it, or enter "
                "OpenAQ Location IDs to skip the automatic lookup."
            )
            return None
        # OpenAQ uses at most 4 decimal places and truncates the rest.
        return round(lat, 4), round(lon, 4)

    def __parse_radius(self, raw):
        text = str("" if raw is None else raw).strip().replace(",", ".")
        if text == "":
            Domoticz.Log(
                "Radius is empty; using the default of {} km".format(DEFAULT_RADIUS_KM)
            )
            return DEFAULT_RADIUS_KM
        try:
            km = float(text)
        except ValueError:
            km = None
        if km is None or not math.isfinite(km) or km <= 0:
            Domoticz.Log(
                "Radius '{}' is not a positive number; using the default of {} km".format(
                    self.__echo(raw), DEFAULT_RADIUS_KM
                )
            )
            return DEFAULT_RADIUS_KM
        if km > MAX_RADIUS_KM:
            Domoticz.Log(
                "Radius {:g} km is above the OpenAQ maximum of {} km; using {} km".format(
                    km, MAX_RADIUS_KM, MAX_RADIUS_KM
                )
            )
            return MAX_RADIUS_KM
        return km

    def __parse_max_stations(self, raw):
        # "Max auto-detected stations". The form offers 3. A field that is
        # blank, which is what an instance created by 4.1 or earlier has until
        # its hardware page is saved, means 1: that is what those versions did,
        # and it keeps an upgrade from adding devices.
        text = str("" if raw is None else raw).strip()
        if text == "":
            Domoticz.Log(
                "Max auto-detected stations is empty; using 1. Enter a number "
                "on the hardware page to detect more (the default is {})".format(
                    DEFAULT_MAX_AUTO_STATIONS
                )
            )
            return 1
        if re.fullmatch(r"[0-9]{1,6}", text) is None or int(text) < 1:
            Domoticz.Log(
                "Max auto-detected stations '{}' is not a positive whole number; "
                "using the default of {}".format(
                    self.__echo(raw), DEFAULT_MAX_AUTO_STATIONS
                )
            )
            return DEFAULT_MAX_AUTO_STATIONS
        count = int(text)
        if count > MAX_STATIONS:
            Domoticz.Log(
                "Max auto-detected stations {} is above the maximum of {}; using {}".format(
                    count, MAX_STATIONS, MAX_STATIONS
                )
            )
            return MAX_STATIONS
        return count

    def __parse_location_ids(self, raw):
        # Returns None when the field is blank, otherwise the valid IDs in the
        # order entered, without duplicates (possibly an empty list).
        text = str("" if raw is None else raw).strip()
        if text == "":
            return None
        ids = []
        for token in text.split(","):
            token = token.strip()
            if token == "":
                continue
            if LOCATION_ID_RE.fullmatch(token) is None or int(token) <= 0:
                Domoticz.Error(
                    "OpenAQ Location ID '{}' is not a positive whole number and "
                    "is ignored".format(self.__echo(token))
                )
                continue
            location_id = int(token)
            if location_id in ids:
                Domoticz.Log(
                    "OpenAQ Location ID {} is listed more than once; using it once".format(
                        location_id
                    )
                )
                continue
            ids.append(location_id)
        if len(ids) > MAX_STATIONS:
            Domoticz.Error(
                "Too many OpenAQ Location IDs ({} entered); only the first {} are used".format(
                    len(ids), MAX_STATIONS
                )
            )
            ids = ids[:MAX_STATIONS]
        return ids

    # ------------------------------------------------------------------
    # Station blocks. A station's devices are found again through the
    # location ID they carry, never through the position in the list.
    # ------------------------------------------------------------------
    @staticmethod
    def __block_units(block):
        first = block * UNITS_PER_STATION + 1
        return [u for u in range(first, first + UNITS_PER_STATION) if u in Devices]

    def __block_owner(self, block, units):
        # 1. a device tagged by 4.2 names its location
        for unit in units:
            match = DEVICE_ID_RE.fullmatch(str(Devices[unit].DeviceID))
            if match and int(match.group(2)) == unit - block * UNITS_PER_STATION:
                return int(match.group(1))
        # 2. otherwise the Info device does (devices created by 4.0 and 4.1)
        info_unit = block * UNITS_PER_STATION + INFO_UNIT
        if info_unit in Devices:
            match = INFO_TEXT_RE.search(str(Devices[info_unit].sValue))
            if match:
                return int(match.group(1))
        return None  # devices exist, but nothing says whom they belong to

    def __scan_blocks(self):
        # block -> OpenAQ location ID it belongs to (None when unknown), for
        # every block that has devices.
        owners = {}
        for block in range(MAX_STATIONS):
            units = self.__block_units(block)
            if units:
                owners[block] = self.__block_owner(block, units)
        return owners

    def __assign_blocks(self, ids):
        # Returns location ID -> block (None when no block is free). Nothing
        # depends on the order of the list or on which lookups succeed.
        owners = self.__scan_blocks()
        by_owner = {}
        for block in sorted(owners):
            if owners[block] is not None and owners[block] not in by_owner:
                by_owner[owners[block]] = block
        unclaimed = sorted(b for b, owner in owners.items() if owner is None)
        used = set(owners)
        assigned = {}
        for location_id in ids:
            if location_id in by_owner:
                assigned[location_id] = by_owner[location_id]
                continue
            if unclaimed:
                # Devices without a recognisable owner (created by an earlier
                # version and never updated) go to the first station without
                # a block: they have no history to protect.
                block = unclaimed.pop(0)
                Domoticz.Debug(
                    "Units {}-{} have no owner; they are used for OpenAQ location {}".format(
                        block * UNITS_PER_STATION + 1, (block + 1) * UNITS_PER_STATION, location_id
                    )
                )
            else:
                block = next((b for b in range(MAX_STATIONS) if b not in used), None)
            if block is not None:
                used.add(block)
            assigned[location_id] = block
        for block in sorted(owners):
            owner = owners[block]
            if owner is not None and owner not in ids:
                Domoticz.Log(
                    "Units {}-{} belong to OpenAQ location {}, which is not in use now. "
                    "Those devices are left unchanged and keep their history; delete them "
                    "in Domoticz to free the slot.".format(
                        block * UNITS_PER_STATION + 1, (block + 1) * UNITS_PER_STATION, owner
                    )
                )
        return assigned

    # ------------------------------------------------------------------
    # Automatic lookup
    # ------------------------------------------------------------------
    def __discover_location_ids(self, lat, lon, radius_km, count):
        radius_m = max(1, int(round(radius_km * 1000)))
        radius_km = radius_m / 1000.0  # log exactly what is sent to OpenAQ
        qs = urllib.parse.urlencode(
            {
                "coordinates": "{},{}".format(lat, lon),
                "radius": radius_m,
                "limit": DISCOVERY_PAGE_SIZE,
            }
        )
        Domoticz.Log(
            "Looking for up to {} OpenAQ location(s) within {:g} km of the Domoticz location".format(
                count, radius_km
            )
        )
        data = self.__blocking_get("/v3/locations?{}".format(qs))
        results = data.get("results") if isinstance(data, dict) else None
        if not results:
            Domoticz.Error(
                "No OpenAQ location found within {:g} km of the Domoticz location. "
                "Increase the radius (maximum {} km) or enter OpenAQ Location IDs.".format(
                    radius_km, MAX_RADIUS_KM
                )
            )
            return []
        candidates = []
        for loc in results:
            if not isinstance(loc, dict) or not is_valid_location_id(loc.get("id")):
                continue
            if loc["id"] not in [c["id"] for c in candidates]:
                candidates.append(loc)
        if not candidates:
            Domoticz.Error("OpenAQ returned a location without an ID")
            return []
        if len(results) >= DISCOVERY_PAGE_SIZE:
            Domoticz.Log(
                "OpenAQ returned its maximum of {} locations, so there may be more within "
                "the radius. Use a smaller radius to rank all of them.".format(
                    DISCOVERY_PAGE_SIZE
                )
            )
        ordered, ranked = rank_by_distance(candidates, lat, lon)
        # Stations that already have devices come first: their history goes on
        # even when the neighbourhood changes. The remaining places go to the
        # nearest others.
        in_use = set(o for o in self.__scan_blocks().values() if o is not None)
        ordered = [l for l in ordered if l["id"] in in_use] + [
            l for l in ordered if l["id"] not in in_use
        ]
        chosen = ordered[:count]
        if ranked:
            Domoticz.Log(
                "OpenAQ returned {} location(s); the nearest ones are used.".format(len(candidates))
            )
        else:
            Domoticz.Log(
                "OpenAQ returned {} location(s) without usable distances; the first ones "
                "returned are used, which are not necessarily the nearest.".format(
                    len(candidates)
                )
            )
        for number, loc in enumerate(chosen, 1):
            Domoticz.Log(
                "Auto-detected OpenAQ location {} of {}: '{}' (id {}){}".format(
                    number,
                    len(chosen),
                    clean_name(loc.get("name"), loc["id"]),
                    loc["id"],
                    ", kept because it already has devices" if loc["id"] in in_use else "",
                )
            )
        ids = [loc["id"] for loc in chosen]
        Domoticz.Log(
            "To keep using the auto-detected location(s), enter {} in the "
            "'OpenAQ Location IDs' hardware field.".format(",".join(str(i) for i in ids))
        )
        return ids

    def __load_station(self, station):
        # sensorsId -> parameter/unit mapping needed to interpret /latest, and
        # the name of the station.
        data = self.__blocking_get("/v3/locations/{}".format(station.id))
        results = data.get("results") if isinstance(data, dict) else None
        if not results or not isinstance(results[0], dict):
            Domoticz.Error(
                "Could not fetch sensor list for OpenAQ location {}".format(station.id)
            )
            return False
        loc = results[0]
        mapping = {}
        for s in loc.get("sensors") or []:
            if not isinstance(s, dict) or s.get("id") is None:
                continue
            param = s.get("parameter", {}) or {}
            name = param.get("name")
            units = param.get("units")
            if name:
                mapping[s["id"]] = {"parameter": name, "units": units}
        if not mapping:
            Domoticz.Error(
                "Location {} returned no usable sensors".format(station.id)
            )
            return False
        Domoticz.Debug("Sensor map for location {}: {}".format(station.id, mapping))
        station.sensor_map = mapping
        station.name = clean_name(loc.get("name"), station.id)
        return True

    def __set_up_stations(self, ids):
        blocks = self.__assign_blocks(ids)
        failed = []
        for index, location_id in enumerate(ids):
            if self.__stop_lookups:
                remaining = ids[index:]
                Domoticz.Error(
                    "Skipping the remaining {} OpenAQ location(s): {}.".format(
                        len(remaining), self.__stop_lookups
                    )
                )
                failed.extend(remaining)
                break
            if blocks[location_id] is None:
                Domoticz.Error(
                    "No free slot for OpenAQ location {}: all {} station slots are in use. "
                    "The slot of a station that is no longer used stays reserved to keep "
                    "its history; delete that station's devices in Domoticz to free it.".format(
                        location_id, MAX_STATIONS
                    )
                )
                failed.append(location_id)
                continue
            station = Station(location_id, blocks[location_id])
            if self.__load_station(station):
                self.__stations[location_id] = station
                Domoticz.Debug(
                    "OpenAQ station '{}' (id {}) uses units {}-{}".format(
                        station.name, location_id, station.unit(1), station.unit(UNITS_PER_STATION)
                    )
                )
            else:
                failed.append(location_id)
        if not self.__stations:
            return False
        if failed:
            Domoticz.Error(
                "OpenAQ started with {} of {} stations. Not configured: {}. They are not "
                "retried until the hardware is restarted or updated.".format(
                    len(self.__stations), len(ids), ", ".join(str(i) for i in failed)
                )
            )
        # With several stations the device names say which station they belong to
        self.__multiple = len(ids) > 1
        names = [s.name.lower() for s in self.__stations.values()]
        for station in self.__stations.values():
            if names.count(station.name.lower()) > 1:
                station.label = "{} #{}".format(station.name, station.id)
            else:
                station.label = station.name
        return True

    def __configure(self):
        # Validates the hardware settings and does the startup lookups.
        # Returns True when the plugin can run. Otherwise the reason has
        # been logged and False is returned.
        self.__API_KEY = (Parameters.get("Mode2") or "").strip()
        if self.__API_KEY == "":
            Domoticz.Error("Unable to read openaq API-KEY from settings")
            return False

        # The OpenAQ locations: the IDs entered, else auto-detect once
        ids = self.__parse_location_ids(Parameters.get("Mode3"))
        if ids is None:
            # Domoticz's own location, the radius and the maximum number of
            # stations are only needed here
            coordinates = self.__domoticz_coordinates()
            if coordinates is None:
                return False
            radius = self.__parse_radius(Parameters.get("Mode1"))
            count = self.__parse_max_stations(Parameters.get("Mode4"))
            ids = self.__discover_location_ids(coordinates[0], coordinates[1], radius, count)
            if not ids:
                return False
        elif not ids:
            Domoticz.Error(
                "No valid OpenAQ Location ID entered. Enter one or more numbers "
                "separated by commas, or leave the field blank to auto-detect."
            )
            return False

        return self.__set_up_stations(ids)

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------
    def __device_name(self, station, name):
        # One station: the names of 4.0 and 4.1. Several: the station is added.
        if not self.__multiple:
            return name
        return "{} ({})".format(name, station.label)

    def __create_devices(self):
        # Images
        if "xfr_openaq2" not in Images:
            Domoticz.Image("xfr_openaq2.zip").Create()
        image = Images["xfr_openaq2"].ID
        Domoticz.Debug("Image created. ID: {}".format(image))

        # Create devices. The local units 1-10 and their definitions must not
        # change: existing devices and their history depend on them.
        for station in self.__stations.values():
            for key, (local_unit, name, _limits) in POLLUTANTS.items():
                if station.unit(local_unit) not in Devices:
                    Domoticz.Device(
                        Unit=station.unit(local_unit),
                        Name=self.__device_name(station, name),
                        TypeName="Custom",
                        Options={"Custom": "0;µg/m³"},
                        Image=image,
                        Used=1,
                        DeviceID=station.device_id(local_unit),
                    ).Create()
            #
            if station.unit(INFO_UNIT) not in Devices:
                Domoticz.Device(
                    Unit=station.unit(INFO_UNIT),
                    Name=self.__device_name(station, "Info"),
                    TypeName="Text",
                    Used=1,
                    DeviceID=station.device_id(INFO_UNIT),
                ).Create()
            #
            if station.unit(ALERT_UNIT) not in Devices:
                Domoticz.Device(
                    Unit=station.unit(ALERT_UNIT),
                    Name=self.__device_name(station, "Pollutants"),
                    Type=243,
                    Subtype=22,
                    Options={},
                    Used=1,
                    DeviceID=station.device_id(ALERT_UNIT),
                ).Create()
            #
            if station.unit(AQI_UNIT) not in Devices:
                Domoticz.Device(
                    Unit=station.unit(AQI_UNIT),
                    Name=self.__device_name(station, "Air Quality Index"),
                    TypeName="Custom",
                    Options={"Custom": "0;"},
                    Image=image,
                    Used=1,
                    DeviceID=station.device_id(AQI_UNIT),
                ).Create()

    # ------------------------------------------------------------------
    # Domoticz plugin callbacks
    # ------------------------------------------------------------------
    def onStart(self):
        Domoticz.Debug("onStart called")
        self.__reset()
        if Parameters.get("Mode6") == "Debug":
            Domoticz.Debugging(1)
        else:
            Domoticz.Debugging(0)

        if not self.__configure():
            Domoticz.Error(
                "OpenAQ plugin not started - see the message above. It stays idle "
                "until the hardware is restarted or updated."
            )
            return False

        self.__create_devices()
        config_2_log()

        # Setup one async connection per station for the recurring /latest poll
        for station in self.__stations.values():
            station.conn = Domoticz.Connection(
                Name=station.conn_name,
                Transport="TCP/IP",
                Protocol="HTTPS",
                Address=self.__API_ENDPOINT,
                Port="443",
            )
            self.__by_connection[station.conn_name] = station
        self.__started = True
        for station in self.__stations.values():
            station.conn.Connect()
        return True

    def onStop(self):
        Domoticz.Debug("onStop")
        self.__started = False
        for station in self.__stations.values():
            station.forget_readings()

    def onConnect(self, Connection, Status, Description):
        if not self.__started:
            Domoticz.Debug("onConnect ignored: the plugin did not start")
            return
        Domoticz.Debug(
            "onConnect: {}, {}, {}".format(Connection.Name, Status, Description)
        )
        station = self.__by_connection.get(Connection.Name)
        if station is not None and Status == 0:
            self.__send_request(Connection, station)

    def __send_request(self, Connection, station):
        sendData = {
            "Verb": "GET",
            "URL": station.url,
            "Headers": {
                "Host": self.__API_ENDPOINT,
                "User-Agent": "Domoticz/1.0",
                "X-API-Key": self.__API_KEY,
                "Accept": "application/json",
            },
        }
        Connection.Send(sendData)

    def onMessage(self, Connection, Data):
        if not self.__started:
            Domoticz.Debug("onMessage ignored: the plugin did not start")
            return
        Domoticz.Debug(
            "onMessage: {}, {}".format(Connection.Name, self.__scrub(Data))
        )
        station = self.__by_connection.get(Connection.Name)
        if station is None:
            return
        status = int(Data["Status"])
        if status == 200:
            self.__update_station(station, Data)
        elif status == 410:
            Domoticz.Error(
                "{} returned HTTP 410 Gone - this OpenAQ API path has been "
                "retired by OpenAQ. Check for a plugin update.".format(
                    Connection.Name
                )
            )
        else:
            Domoticz.Error(
                "{} returned a status: {}".format(Connection.Name, status)
            )

    def __update_station(self, station, Data):
        values = json.loads(Data["Data"].decode("utf-8", "ignore"))
        results = values.get("results", [])
        totMeasurements = len(results)
        totSensorsKnown = 0
        Domoticz.Debug(
            "Readings received for location {}: {}".format(station.id, totMeasurements)
        )
        for reading in results:
            sensorsId = reading.get("sensorsId")
            info = station.sensor_map.get(sensorsId)
            if info is None:
                # Sensor at this location that we don't track/recognise
                continue
            parameter = info["parameter"]
            if parameter not in POLLUTANTS:
                continue
            unit = info.get("units")
            value = reading.get("value")
            dt = (reading.get("datetime") or {}).get("utc")
            if value is None or dt is None:
                continue
            Domoticz.Debug("{} ... {}: {} {}".format(dt, parameter, value, unit))
            lastUpdated = dt[0:19]
            value = float(value)
            # Fix for Python bug
            try:
                t = datetime.strptime(lastUpdated, "%Y-%m-%dT%H:%M:%S")
            except TypeError:
                t = datetime.fromtimestamp(
                    time.mktime(time.strptime(lastUpdated, "%Y-%m-%dT%H:%M:%S"))
                )
            # Skip sentinel/invalid values like '-999'
            if value > 0.0:
                totSensorsKnown += 1
                current = station.values[parameter]
                # The first value found is always taken, later ones if more actual
                if current[1] is None or t > current[0]:
                    current[0], current[1], current[2] = t, value, unit
        # Update the devices
        level = 0
        txt = ""
        for key, (local_unit, name, limits) in POLLUTANTS.items():
            current = station.values[key]
            if current[1] is None:
                continue
            update_device_options(
                station.unit(local_unit), {"Custom": "0;{}".format(current[2])}
            )
            update_device(
                station.unit(local_unit), int(current[1]), str(round(current[1], 1))
            )
            # Check warning levels of this sensor
            for i in range(4, 0, -1):
                if (  # Warning level available for this sensor
                    limits[i - 1] is not None
                    # Value higher then upper level
                    and current[1] > limits[i - 1]
                ):
                    Domoticz.Debug(
                        "{}: {} > {}?".format(station.unit(local_unit), current[1], limits[i - 1])
                    )
                    # level higher then previous value?
                    if i > level:
                        level = i
                        # Add pollutant to the warning text
                        txt += name + " "
        # Info. The text names the location: 4.2 recognises the station of
        # devices that carry no tag from it, so keep the wording.
        stat = "OpenAQ Location ID: {}<br/>Sensors reporting: {}/{}".format(
            station.id, totSensorsKnown, totMeasurements
        )
        update_device(station.unit(INFO_UNIT), 0, stat)
        # Alert
        Domoticz.Debug("Level: {}".format(level))
        if level == 0:
            update_device(station.unit(ALERT_UNIT), level, "No alert")
        else:
            update_device(station.unit(ALERT_UNIT), level, "{}".format(txt))
        # Index
        update_device(station.unit(AQI_UNIT), level, "{}".format(level))

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug("onCommand: {}, {}, {}, {}".format(Unit, Command, Level, Hue))

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Debug(
            "onNotification: {}, {}, {}, {}, {}, {}, {}".format(
                Name, Subject, Text, Status, Priority, Sound, ImageFile
            )
        )

    def onDisconnect(self, Connection):
        Domoticz.Debug("onDisconnect: {}".format(Connection.Name))

    def onHeartbeat(self):
        Domoticz.Debug("onHeartbeat")
        if not self.__started:
            Domoticz.Debug("onHeartbeat ignored: the plugin did not start")
            return
        # Live
        self.__runAgain -= 1
        if self.__runAgain <= 0:
            for station in self.__stations.values():
                conn = station.conn
                if conn.Connecting() or conn.Connected():
                    Domoticz.Debug("onHeartbeat ({}): is alive".format(conn.Name))
                    self.__send_request(conn, station)
                else:
                    conn.Connect()
            self.__runAgain = self.__HEARTBEATS2MIN * self.__MINUTES
        Domoticz.Debug("onHeartbeat: {} heartbeats".format(self.__runAgain))


global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)


def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)


def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)


def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)


def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()


################################################################################
# Generic helper functions
################################################################################
def is_valid_location_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10 ** 12


def clean_name(name, location_id):
    # The name of an OpenAQ location ends up in Domoticz device names: keep
    # it printable, short and free of markup.
    text = re.sub(r"[<>\x00-\x1f\x7f]", " ", str("" if name is None else name))
    text = " ".join(text.split())[:MAX_NAME_LENGTH].strip()
    return text if text else "Location {}".format(location_id)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def _finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def rank_by_distance(locations, lat, lon):
    # OpenAQ documents no distance ordering for GET /v3/locations, so the
    # locations are ranked here. Returns (locations, ranked). The distance is
    # computed from the coordinates of every location; failing that the
    # "distance" field of OpenAQ is used, but never a mix of both (its unit is
    # undocumented). Without either, the order of OpenAQ is kept and ranked is
    # False.
    distances = []
    for loc in locations:
        coords = loc.get("coordinates")
        distance = None
        if isinstance(coords, dict):
            la, lo = coords.get("latitude"), coords.get("longitude")
            if (
                _finite_number(la)
                and _finite_number(lo)
                and -90.0 <= la <= 90.0
                and -180.0 <= lo <= 180.0
            ):
                distance = haversine_km(lat, lon, la, lo)
        distances.append(distance)
    if not locations or any(d is None for d in distances):
        distances = [loc.get("distance") for loc in locations]
        if not locations or not all(_finite_number(d) and d >= 0 for d in distances):
            return list(locations), False
    order = sorted(range(len(locations)), key=lambda i: distances[i])  # stable
    return [locations[i] for i in order], True


def mask_value(name, value, loggable):
    # Show a value only if its name is on the allow-list; mask everything else.
    if name in loggable:
        return value
    if value is None or str(value) == "":
        return EMPTY
    return MASKED


def config_2_log():
    # Show parameters. Values are masked unless the name is on the allow-list:
    # the API key and the Domoticz location must never reach the log.
    Domoticz.Debug("Parameters count.....: {}".format(len(Parameters)))
    for x in Parameters:
        Domoticz.Debug(
            "Parameter '{}'...: '{}'".format(
                x, mask_value(x, Parameters[x], LOGGABLE_PARAMETERS)
            )
        )
    # Show settings
    Domoticz.Debug("Settings count...: {}".format(len(Settings)))
    for x in Settings:
        Domoticz.Debug(
            "Setting '{}'...: '{}'".format(
                x, mask_value(x, Settings[x], LOGGABLE_SETTINGS)
            )
        )
    # Show images
    Domoticz.Debug("Image count..........: {}".format(len(Images)))
    for x in Images:
        Domoticz.Debug("Image '{}'...': '{}'".format(x, Images[x]))
    # Show devices
    Domoticz.Debug("Device count.........: {}".format(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device...............: {} - {}".format(x, Devices[x]))
        Domoticz.Debug("Device Idx...........: {}".format(Devices[x].ID))
        Domoticz.Debug(
            "Device Type..........: {} / {}".format(Devices[x].Type, Devices[x].SubType)
        )
        Domoticz.Debug("Device Name..........: '{}'".format(Devices[x].Name))
        Domoticz.Debug("Device nValue........: {}".format(Devices[x].nValue))
        Domoticz.Debug("Device sValue........: '{}'".format(Devices[x].sValue))
        Domoticz.Debug("Device Options.......: '{}'".format(Devices[x].Options))
        Domoticz.Debug("Device Used..........: {}".format(Devices[x].Used))
        Domoticz.Debug("Device ID............: '{}'".format(Devices[x].DeviceID))
        Domoticz.Debug("Device LastLevel.....: {}".format(Devices[x].LastLevel))
        Domoticz.Debug("Device Image.........: {}".format(Devices[x].Image))


def update_device(Unit, nValue, sValue, TimedOut=0, AlwaysUpdate=False):
    if Unit in Devices:
        if (
            Devices[Unit].nValue != nValue
            or Devices[Unit].sValue != sValue
            or Devices[Unit].TimedOut != TimedOut
            or AlwaysUpdate
        ):
            Devices[Unit].Update(nValue=nValue, sValue=str(sValue), TimedOut=TimedOut)
            Domoticz.Debug(
                "Update {}: {} - '{}'".format(Devices[Unit].Name, nValue, sValue)
            )


def response_2_log(response):
    if isinstance(response, dict):
        Domoticz.Debug("Response: ({})".format(len(response)))
        for x in response:
            if isinstance(response[x], dict):
                Domoticz.Debug(".... {} ({})".format(x, len(response[x])))
                for y in response[x]:
                    Domoticz.Debug("........ '{}': '{}'".format(y, response[x][y]))
            else:
                Domoticz.Debug(".... '{}': '{}'".format(x, response[x]))


def update_device_options(Unit, Options={}):
    if Unit in Devices:
        Devices[Unit].Update(
            nValue=Devices[Unit].nValue, sValue=Devices[Unit].sValue, Options=Options
        )
        Domoticz.Debug("Update options {}: {}".format(Devices[Unit].Name, Options))
