#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  OpenAQ Python Plugin
#
# Author: Xorfor
# Maintainer: Waltervl, janreimen
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
#        of the nearest location once at startup, using your Domoticz
#        Settings coordinates and the configured radius.
#
# Air Quality Index based on:
#   http://www.airqualitynow.eu/about_indices_definition.php

"""
<plugin key="xfr_openaq4" name="OpenAQ 4" author="Xorfor, Waltervl, janreimen" version="4.0" wikilink="https://github.com/janreimen/Domoticz-OpenAQ-Plugin" externallink="https://openaq.org/">
    <params>
        <param field="Mode1" label="Radius (km, used only if Location ID is blank)" width="75px" default="10" required="false"/>
        <param field="Mode2" label="OpenAQ API-KEY" width="200px" default="" required="true"/>
        <param field="Mode3" label="OpenAQ Location ID (blank = auto-detect nearest)" width="100px" default="" required="false"/>
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
import ssl
import http.client
import urllib.parse
from datetime import datetime
import time


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
        self.__runAgain = 0
        self.__radius = 0
        self.__url = ""
        self.__conn = None
        self.__API_KEY = ""
        self.__locationId = None
        self.__sensorMap = {}  # sensorsId (int) -> {"parameter": "pm25", "units": "µg/m³"}

    # ------------------------------------------------------------------
    # One-off blocking HTTPS helper, used only at onStart for the two
    # setup lookups below. Domoticz's async Connection object (used for
    # the recurring heartbeat poll further down) is the normal pattern for
    # anything repeated, but a single short blocking call at startup is a
    # widely used, accepted simplification for one-time metadata lookups.
    # ------------------------------------------------------------------
    def __blocking_get(self, path):
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
            Domoticz.Debug("Blocking GET {} -> HTTP {}".format(path, status))
            if status != 200:
                Domoticz.Error(
                    "OpenAQ request {} returned HTTP {}: {}".format(
                        path, status, body[:200]
                    )
                )
                return None
            return json.loads(body.decode("utf-8", "ignore"))
        except Exception as e:
            Domoticz.Error("OpenAQ request {} failed: {}".format(path, e))
            return None
        finally:
            conn.close()

    def __discover_location_id(self, lat, lon, radius_m):
        qs = urllib.parse.urlencode(
            {"coordinates": "{},{}".format(lat, lon), "radius": radius_m, "limit": 1}
        )
        data = self.__blocking_get("/v3/locations?{}".format(qs))
        if not data or not data.get("results"):
            Domoticz.Error(
                "No OpenAQ location found within {} m of {},{}".format(radius_m, lat, lon)
            )
            return None
        loc = data["results"][0]
        Domoticz.Log(
            "Auto-selected nearest OpenAQ location: {} (id {})".format(
                loc.get("name", "?"), loc.get("id")
            )
        )
        return loc.get("id")

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

    def onStart(self):
        Domoticz.Debug("onStart called")
        if Parameters["Mode6"] == "Debug":
            Domoticz.Debugging(1)
        else:
            Domoticz.Debugging(0)
        # Images
        if "xfr_openaq2" not in Images:
            Domoticz.Image("xfr_openaq2.zip").Create()
        image = Images["xfr_openaq2"].ID
        Domoticz.Debug("Image created. ID: {}".format(image))

        # API key
        self.__API_KEY = Parameters["Mode2"]
        if self.__API_KEY == "":
            Domoticz.Error("Unable to read openaq API-KEY from settings")
            return False

        # Radius (only used for auto-discovery fallback, when Mode3 is blank)
        try:
            self.__radius = int(Parameters["Mode1"])
        except (ValueError, KeyError):
            self.__radius = 10
        if self.__radius <= 0:
            self.__radius = 10
        radius_m = self.__radius * 1000

        # Domoticz's own location, used only for auto-discovery
        loc = Settings["Location"].split(";")
        lat = loc[0]
        lon = loc[1]
        if lat is None or lon is None:
            Domoticz.Error("Unable to parse coordinates")
            return False

        # Resolve the OpenAQ Location ID: explicit param wins, else auto-detect once
        locId = Parameters.get("Mode3", "").strip()
        if locId:
            try:
                self.__locationId = int(locId)
            except ValueError:
                Domoticz.Error(
                    "OpenAQ Location ID must be numeric, got '{}'".format(locId)
                )
                return False
        else:
            self.__locationId = self.__discover_location_id(lat, lon, radius_m)
            if self.__locationId is None:
                return False

        # Resolve sensorsId -> parameter/unit mapping needed to interpret /latest
        if not self.__load_sensor_map(self.__locationId):
            return False

        self.__url = self.__API_URL_LATEST.format(self.__locationId)
        Domoticz.Debug("url: {}".format(self.__url))

        # Create devices
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
        #
        config_2_log()
        # Setup async connection for the recurring /latest poll
        self.__conn = Domoticz.Connection(
            Name=self.__API_CONN,
            Transport="TCP/IP",
            Protocol="HTTPS",
            Address=self.__API_ENDPOINT,
            Port="443",
        )
        self.__conn.Connect()

    def onStop(self):
        Domoticz.Debug("onStop")
        for id in self.__VALUES:
            self.__VALUES[id][0] = None
            self.__VALUES[id][1] = None

    def onConnect(self, Connection, Status, Description):
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
        Domoticz.Debug("onMessage: {}, {}".format(Connection.Name, Data))
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
def config_2_log():
    # Show parameters
    Domoticz.Debug("Parameters count.....: {}".format(len(Parameters)))
    for x in Parameters:
        Domoticz.Debug("Parameter '{}'...: '{}'".format(x, Parameters[x]))
    # Show settings
    Domoticz.Debug("Settings count...: {}".format(len(Settings)))
    for x in Settings:
        Domoticz.Debug("Setting '{}'...: '{}'".format(x, Settings[x]))
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
