#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  OpenAQ Python Plugin
#
# Author: Xorfor
# Maintainer: Waltervl, janreimen
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
<plugin key="xfr_openaq4" name="OpenAQ 4" author="Xorfor, Waltervl, janreimen" version="4.1" wikilink="https://github.com/janreimen/Domoticz-OpenAQ-Plugin" externallink="https://openaq.org/">
    <params>
        <param field="Mode1" label="Radius (km, max 25, used only if Location ID is blank)" width="75px" default="10" required="false"/>
        <param field="Mode2" label="OpenAQ API-KEY" width="200px" default="" required="true" password="true"/>
        <param field="Mode3" label="OpenAQ Location ID (blank = auto-detect)" width="100px" default="" required="false"/>
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
import ssl
import http.client
import urllib.parse
from datetime import datetime
import time

# Radius of the automatic location lookup. OpenAQ documents a maximum of
# 25,000 m for the "radius" parameter of GET /v3/locations.
DEFAULT_RADIUS_KM = 10
MAX_RADIUS_KM = 25

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
        "Mode3",  # location ID
        "Mode6",  # debug
    ]
)
LOGGABLE_SETTINGS = frozenset(["Language"])


class BasePlugin:

    __HEARTBEATS2MIN = 6
    __MINUTES = 60  # 1 hour or use a parameter

    __API_CONN = "openaq"
    __API_ENDPOINT = "api.openaq.org"
    __API_URL_LATEST = "/v3/locations/{}/latest"

    __LEVELS = {0: "Very low", 1: "Low", 2: "Medium", 3: "High", 4: "Very high"}
    __VALUES = {
        # id: [date, value, unit, name, units, low, medium, high, very high]
        "bc": [None, None, 1, "BC", None, None, None, None, None],
        "co": [None, None, 2, "CO", None, 5000, 7500, 10000, 20000],
        "no2": [None, None, 3, "NO<sub>2</sub>", None, 50, 100, 200, 400],
        "o3": [None, None, 4, "O<sub>3</sub>", None, 60, 120, 180, 240],
        "pm10": [None, None, 5, "PM<sub>10</sub>", None, 25, 50, 90, 180],
        "pm25": [None, None, 6, "PM<sub>2.5</sub>", None, 15, 30, 55, 110],
        "so2": [None, None, 7, "SO<sub>2</sub>", None, 50, 100, 350, 500],
    }

    def __init__(self):
        self.__radius = 0
        self.__API_KEY = ""
        self.__reset()

    def __reset(self):
        # State of a (re)start. Everything here is rebuilt by onStart.
        self.__started = False  # True only once onStart has completed
        self.__runAgain = 0
        self.__url = ""
        self.__conn = None
        self.__locationId = None
        self.__sensorMap = {}  # sensorsId (int) -> {"parameter": "pm25", "units": "µg/m³"}

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

    @staticmethod
    def __loggable_path(path):
        # The automatic lookup puts the Domoticz coordinates in the query
        # string, so only the path is ever logged.
        base, separator, _query = path.partition("?")
        return base + ("?<query hidden>" if separator else "")

    # ------------------------------------------------------------------
    # One-off blocking HTTPS helper, used only at onStart for the two
    # setup lookups below. Domoticz's async Connection object (used for
    # the recurring heartbeat poll further down) is the normal pattern for
    # anything repeated, but a single short blocking call at startup is a
    # widely used, accepted simplification for one-time metadata lookups.
    # ------------------------------------------------------------------
    def __blocking_get(self, path):
        shown = self.__loggable_path(path)
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(self.__API_ENDPOINT, 443, timeout=10, context=ctx)
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
            Domoticz.Debug("Blocking GET {} -> HTTP {}".format(shown, status))
            if status != 200:
                Domoticz.Error(
                    "OpenAQ request {} returned HTTP {}: {}".format(
                        shown, status, self.__scrub(body[:200].decode("utf-8", "ignore"))
                    )
                )
                return None
            return json.loads(body.decode("utf-8", "ignore"))
        except Exception as e:
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
                "(Setup > Settings > System > Location). Set it, or enter an "
                "OpenAQ Location ID to skip the automatic lookup."
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
                    raw, DEFAULT_RADIUS_KM
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

    def __discover_location_id(self, lat, lon, radius_km):
        radius_m = max(1, int(round(radius_km * 1000)))
        radius_km = radius_m / 1000.0  # log exactly what is sent to OpenAQ
        qs = urllib.parse.urlencode(
            {"coordinates": "{},{}".format(lat, lon), "radius": radius_m, "limit": 1}
        )
        Domoticz.Log(
            "Looking for an OpenAQ location within {:g} km of the Domoticz location".format(
                radius_km
            )
        )
        data = self.__blocking_get("/v3/locations?{}".format(qs))
        if not data or not data.get("results"):
            Domoticz.Error(
                "No OpenAQ location found within {:g} km of the Domoticz location. "
                "Increase the radius (maximum {} km) or enter an OpenAQ Location ID.".format(
                    radius_km, MAX_RADIUS_KM
                )
            )
            return None
        loc = data["results"][0]
        location_id = loc.get("id")
        if location_id is None:
            Domoticz.Error("OpenAQ returned a location without an ID")
            return None
        Domoticz.Log(
            "Auto-selected OpenAQ location '{}' (id {}): the first location OpenAQ "
            "returned within {:g} km, which is not necessarily the nearest one.".format(
                loc.get("name", "?"), location_id, radius_km
            )
        )
        Domoticz.Log(
            "To keep using this location, enter {} in the 'OpenAQ Location ID' "
            "hardware field.".format(location_id)
        )
        return location_id

    def __load_sensor_map(self, location_id):
        data = self.__blocking_get("/v3/locations/{}".format(location_id))
        if not data or not data.get("results"):
            Domoticz.Error(
                "Could not fetch sensor list for OpenAQ location {}".format(location_id)
            )
            return False
        loc = data["results"][0]
        sensors = loc.get("sensors", [])
        mapping = {}
        for s in sensors:
            param = s.get("parameter", {}) or {}
            name = param.get("name")
            units = param.get("units")
            if name:
                mapping[s["id"]] = {"parameter": name, "units": units}
        if not mapping:
            Domoticz.Error(
                "Location {} returned no usable sensors".format(location_id)
            )
            return False
        Domoticz.Debug("Sensor map for location {}: {}".format(location_id, mapping))
        self.__sensorMap = mapping
        return True

    def __configure(self):
        # Validates the hardware settings and does the startup lookups.
        # Returns True when the plugin can run. Otherwise the reason has
        # been logged and False is returned.
        self.__API_KEY = (Parameters.get("Mode2") or "").strip()
        if self.__API_KEY == "":
            Domoticz.Error("Unable to read openaq API-KEY from settings")
            return False

        # Resolve the OpenAQ Location ID: explicit param wins, else auto-detect once
        locId = (Parameters.get("Mode3") or "").strip()
        if locId:
            try:
                self.__locationId = int(locId)
            except ValueError:
                Domoticz.Error(
                    "OpenAQ Location ID must be numeric, got '{}'".format(locId)
                )
                return False
            if self.__locationId <= 0:
                self.__locationId = None
                Domoticz.Error(
                    "OpenAQ Location ID must be a positive number, got '{}'".format(locId)
                )
                return False
        else:
            # Domoticz's own location and the radius are only needed here
            coordinates = self.__domoticz_coordinates()
            if coordinates is None:
                return False
            self.__radius = self.__parse_radius(Parameters.get("Mode1"))
            self.__locationId = self.__discover_location_id(
                coordinates[0], coordinates[1], self.__radius
            )
            if self.__locationId is None:
                return False

        # Resolve sensorsId -> parameter/unit mapping needed to interpret /latest
        if not self.__load_sensor_map(self.__locationId):
            return False

        self.__url = self.__API_URL_LATEST.format(self.__locationId)
        Domoticz.Debug("url: {}".format(self.__url))
        return True

    def __create_devices(self):
        # Images
        if "xfr_openaq2" not in Images:
            Domoticz.Image("xfr_openaq2.zip").Create()
        image = Images["xfr_openaq2"].ID
        Domoticz.Debug("Image created. ID: {}".format(image))

        # Create devices. Units 1-10 and their definitions must not change:
        # existing devices and their history depend on them.
        for id in self.__VALUES:
            if self.__VALUES[id][2] not in Devices:
                self.__VALUES[id][0] = None
                self.__VALUES[id][1] = None
                Domoticz.Device(
                    Unit=self.__VALUES[id][2],
                    Name=self.__VALUES[id][3],
                    TypeName="Custom",
                    Options={"Custom": "0;µg/m³"},
                    Image=image,
                    Used=1,
                ).Create()
        #
        unit = len(self.__VALUES)
        #
        unit += 1
        if unit not in Devices:
            Domoticz.Device(Unit=unit, Name="Info", TypeName="Text", Used=1).Create()
        #
        unit += 1
        if unit not in Devices:
            Domoticz.Device(
                Unit=unit,
                Name="Pollutants",
                Type=243,
                Subtype=22,
                Options={},
                Used=1,
            ).Create()
        #
        unit += 1
        if unit not in Devices:
            Domoticz.Device(
                Unit=unit,
                Name="Air Quality Index",
                TypeName="Custom",
                Options={"Custom": "0;"},
                Image=image,
                Used=1,
            ).Create()

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

        # Setup async connection for the recurring /latest poll
        self.__conn = Domoticz.Connection(
            Name=self.__API_CONN,
            Transport="TCP/IP",
            Protocol="HTTPS",
            Address=self.__API_ENDPOINT,
            Port="443",
        )
        self.__started = True
        self.__conn.Connect()
        return True

    def onStop(self):
        Domoticz.Debug("onStop")
        self.__started = False
        for id in self.__VALUES:
            self.__VALUES[id][0] = None
            self.__VALUES[id][1] = None

    def onConnect(self, Connection, Status, Description):
        if not self.__started:
            Domoticz.Debug("onConnect ignored: the plugin did not start")
            return
        Domoticz.Debug(
            "onConnect: {}, {}, {}".format(Connection.Name, Status, Description)
        )
        if Connection.Name == self.__API_CONN:
            if Status == 0:
                self.__send_request(Connection)

    def __send_request(self, Connection):
        sendData = {
            "Verb": "GET",
            "URL": self.__url,
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
        status = int(Data["Status"])
        if Connection.Name == self.__API_CONN:
            if status == 200:
                values = json.loads(Data["Data"].decode("utf-8", "ignore"))
                results = values.get("results", [])
                totMeasurements = len(results)
                totSensorsKnown = 0
                Domoticz.Debug("Readings received: {}".format(totMeasurements))
                for reading in results:
                    sensorsId = reading.get("sensorsId")
                    info = self.__sensorMap.get(sensorsId)
                    if info is None:
                        # Sensor at this location that we don't track/recognise
                        continue
                    parameter = info["parameter"]
                    if parameter not in self.__VALUES:
                        continue
                    unit = info.get("units")
                    value = reading.get("value")
                    dt = (reading.get("datetime") or {}).get("utc")
                    if value is None or dt is None:
                        continue
                    Domoticz.Debug(
                        "{} ... {}: {} {}".format(dt, parameter, value, unit)
                    )
                    lastUpdated = dt[0:19]
                    value = float(value)
                    # Fix for Python bug
                    try:
                        t = datetime.strptime(lastUpdated, "%Y-%m-%dT%H:%M:%S")
                    except TypeError:
                        t = datetime.fromtimestamp(
                            time.mktime(
                                time.strptime(lastUpdated, "%Y-%m-%dT%H:%M:%S")
                            )
                        )
                    # Skip sentinel/invalid values like '-999'
                    if value > 0.0:
                        totSensorsKnown += 1
                        if self.__VALUES[parameter][1] is None:
                            # First time value found. Always get this one.
                            self.__VALUES[parameter][0] = t
                            self.__VALUES[parameter][1] = value
                            self.__VALUES[parameter][4] = unit
                        else:
                            # Is this value more actual?
                            if t > self.__VALUES[parameter][0]:
                                self.__VALUES[parameter][0] = t
                                self.__VALUES[parameter][1] = value
                                self.__VALUES[parameter][4] = unit
                # Update the devices
                level = 0
                txt = ""
                for id in self.__VALUES:
                    if self.__VALUES[id][1] is not None:
                        update_device_options(
                            self.__VALUES[id][2],
                            {"Custom": "0;{}".format(self.__VALUES[id][4])},
                        )
                        update_device(
                            self.__VALUES[id][2],
                            int(self.__VALUES[id][1]),
                            str(round(self.__VALUES[id][1], 1)),
                        )
                        # Check warning levels of this sensor
                        offset = 4
                        for i in range(4, 0, -1):
                            if (  # Warning level available for this sensor
                                self.__VALUES[id][offset + i] is not None
                                # Value higher then upper level
                                and self.__VALUES[id][1] > self.__VALUES[id][offset + i]
                            ):
                                Domoticz.Debug(
                                    "{}: {} > {}?".format(
                                        self.__VALUES[id][2],
                                        self.__VALUES[id][1],
                                        self.__VALUES[id][offset + i],
                                    )
                                )
                                # level higher then previous value?
                                if i > level:
                                    level = i
                                    # Add pollutant to the warning text
                                    txt += self.__VALUES[id][3] + " "
                # Info
                unit = len(self.__VALUES)
                unit += 1
                stat = "OpenAQ Location ID: {}<br/>Sensors reporting: {}/{}".format(
                    self.__locationId, totSensorsKnown, totMeasurements
                )
                update_device(unit, 0, stat)
                # Alert
                Domoticz.Debug("Level: {}".format(level))
                unit += 1
                if level == 0:
                    update_device(unit, level, "No alert")
                else:
                    update_device(unit, level, "{}".format(txt))
                # Index
                unit += 1
                update_device(unit, level, "{}".format(level))
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
        Domoticz.Debug("url: {}".format(self.__url))
        # Live
        self.__runAgain -= 1
        if self.__runAgain <= 0:
            if self.__conn.Connecting() or self.__conn.Connected():
                Domoticz.Debug("onHeartbeat ({}): is alive".format(self.__conn.Name))
                self.__send_request(self.__conn)
            else:
                self.__conn.Connect()
            self.__runAgain = self.__HEARTBEATS2MIN * self.__MINUTES
        Domoticz.Debug(
            "onHeartbeat ({}): {} heartbeats".format(self.__conn.Name, self.__runAgain)
        )


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
