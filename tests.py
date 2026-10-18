#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test suite for Domoticz-OpenAQ-Plugin.

Standard library only. No Domoticz installation and no network access are
needed: the Domoticz plugin API (the ``Domoticz`` module plus the globals
``Parameters``, ``Settings``, ``Images`` and ``Devices`` that Domoticz injects
into a plugin) is replaced by a small in-memory fake, and every HTTP call is
mocked. The tests import and exercise the real ``plugin.py``.

Run from the repository root:

    python3 tests.py

For a basic syntax check without running anything:

    python3 -m py_compile plugin.py tests.py
"""
import ast
import importlib.util
import json
import os
import re
import ssl
import sys
import types
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_PATH = os.path.join(HERE, "plugin.py")

# Domoticz unit numbers created by the plugin
UNIT_BC, UNIT_CO, UNIT_NO2, UNIT_O3, UNIT_PM10, UNIT_PM25, UNIT_SO2 = range(1, 8)
UNIT_INFO, UNIT_POLLUTANTS, UNIT_AQI = 8, 9, 10


# ---------------------------------------------------------------------------
# Fake Domoticz plugin API
# ---------------------------------------------------------------------------
class FakeDevice:
    def __init__(self, owner, Unit, Name, TypeName=None, Type=None, Subtype=None,
                 Options=None, Image=None, Used=0, DeviceID=None, **kwargs):
        self.owner = owner
        self.Unit = Unit
        self.Name = Name
        self.TypeName = TypeName
        self.Type = Type or 0
        self.SubType = Subtype or 0
        self.Options = dict(Options or {})
        self.Image = Image
        self.Used = Used
        self.nValue = 0
        self.sValue = ""
        self.TimedOut = 0
        self.ID = 1000 + Unit
        # Domoticz derives a DeviceID from the unit when none is given
        self.DeviceID = DeviceID if DeviceID is not None else "%08X" % Unit
        self.LastLevel = 0
        self.update_count = 0

    def Create(self):
        self.owner.devices[self.Unit] = self
        self.owner.created_units.append(self.Unit)

    def Update(self, nValue, sValue, TimedOut=0, Options=None, **kwargs):
        self.nValue = nValue
        self.sValue = sValue
        self.TimedOut = TimedOut
        if Options is not None:
            self.Options = dict(Options)
        self.update_count += 1

    def __str__(self):
        return "FakeDevice(%s)" % self.Name


class FakeImage:
    def __init__(self, owner, filename):
        self.owner = owner
        self.filename = filename

    def Create(self):
        key = os.path.splitext(self.filename)[0]
        self.owner.images[key] = types.SimpleNamespace(
            ID=len(self.owner.images) + 1, Name=key)


class FakeConnection:
    def __init__(self, Name, Transport=None, Protocol=None, Address=None,
                 Port=None, **kwargs):
        self.Name = Name
        self.Transport = Transport
        self.Protocol = Protocol
        self.Address = Address
        self.Port = Port
        self.connect_calls = 0
        self.sent = []
        self._connecting = False
        self._connected = False

    def Connect(self):
        self.connect_calls += 1
        self._connecting = True

    def Connecting(self):
        return self._connecting

    def Connected(self):
        return self._connected

    def Send(self, data):
        self.sent.append(data)


class FakeDomoticz(types.ModuleType):
    """Stands in for the ``Domoticz`` module that Domoticz provides."""

    def __init__(self):
        super().__init__("Domoticz")
        self.debug_lines = []
        self.log_lines = []
        self.error_lines = []
        self.debugging = None
        self.devices = {}
        self.images = {}
        self.connections = []
        self.created_units = []

    def Debug(self, msg):
        self.debug_lines.append(str(msg))

    def Log(self, msg):
        self.log_lines.append(str(msg))

    def Error(self, msg):
        self.error_lines.append(str(msg))

    def Debugging(self, flag):
        self.debugging = flag

    def Device(self, **kwargs):
        return FakeDevice(self, **kwargs)

    def Image(self, filename):
        return FakeImage(self, filename)

    def Connection(self, **kwargs):
        conn = FakeConnection(**kwargs)
        self.connections.append(conn)
        return conn


# ---------------------------------------------------------------------------
# Sample OpenAQ v3 payloads
# ---------------------------------------------------------------------------
def sensor(sensor_id, name, units="µg/m³"):
    return {"id": sensor_id, "parameter": {"name": name, "units": units}}


def reading(sensor_id, value, utc="2026-10-16T05:00:00Z"):
    return {"sensorsId": sensor_id, "value": value, "datetime": {"utc": utc}}


def location(*sensors):
    return {"results": [{"id": 9645, "name": "Test station", "sensors": list(sensors)}]}


def default_location():
    return location(
        sensor(11, "pm25"), sensor(12, "pm10"), sensor(13, "no2"),
        sensor(14, "o3"), sensor(15, "temperature", "c"),
    )


def station_location(location_id, name, sensors=None):
    """GET /v3/locations/{id} for one station (the sensor ids 11-15 are shared by
    all test stations: a station's connection decides whose readings these are)."""
    base = default_location()["results"][0]
    return {"results": [dict(base, id=location_id, name=name,
                             sensors=list(sensors) if sensors is not None else base["sensors"])]}


def found(location_id, name=None, lat=None, lon=None, distance=None):
    """One result of the automatic lookup GET /v3/locations?coordinates=..."""
    entry = {"id": location_id, "name": name if name is not None else "Station %s" % location_id}
    if lat is not None:
        entry["coordinates"] = {"latitude": lat, "longitude": lon}
    if distance is not None:
        entry["distance"] = distance
    return entry


# ---------------------------------------------------------------------------
# Base class: loads a fresh copy of plugin.py for every test
# ---------------------------------------------------------------------------
class PluginTestCase(unittest.TestCase):
    PARAMS = {"Mode1": "10", "Mode2": "TEST-API-KEY", "Mode3": "9645", "Mode6": "Normal"}
    SETTINGS = {"Location": "49.61;6.13"}
    PATCH_BLOCKING_GET = True

    def setUp(self):
        self.fake = FakeDomoticz()
        sys.modules["Domoticz"] = self.fake
        self.addCleanup(sys.modules.pop, "Domoticz", None)

        spec = importlib.util.spec_from_file_location("openaq_plugin_under_test", PLUGIN_PATH)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

        # Globals that Domoticz injects into a plugin module
        self.mod.Parameters = dict(self.PARAMS)
        self.mod.Settings = dict(self.SETTINGS)
        self.mod.Images = self.fake.images
        self.mod.Devices = self.fake.devices

        self.plugin = self.mod.BasePlugin()
        self.api_calls = []
        self.search_response = {"results": [{"id": 9645, "name": "Test station"}]}
        self.location_response = default_location()
        self.locations = {}  # location ID -> payload (None = the lookup fails)

        if self.PATCH_BLOCKING_GET:
            patcher = mock.patch.object(
                self.mod.BasePlugin, "_BasePlugin__blocking_get",
                side_effect=self._fake_get)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fake_get(self, path):
        self.api_calls.append(path)
        if path.startswith("/v3/locations?"):
            return self.search_response
        match = re.match(r"^/v3/locations/(\d+)$", path)
        if match:
            return self.location_for(int(match.group(1)))
        raise AssertionError("unexpected API path: %s" % path)

    def location_for(self, location_id):
        if location_id in self.locations:
            return self.locations[location_id]
        if location_id == 9645 or not self.location_response:
            return self.location_response
        # any other station: the same sensors under its own ID and name
        return {"results": [dict(self.location_response["results"][0], id=location_id,
                                 name="Station %d" % location_id)]}

    # helpers --------------------------------------------------------------
    def start_default(self):
        result = self.plugin.onStart()
        self.assertIsNot(result, False, "onStart unexpectedly aborted: %s"
                         % self.fake.error_lines)
        return result

    def deliver(self, payload, status=200, to=None):
        """Answer the poll of one station (default: the first connection)."""
        conn = self.fake.connections[0] if to is None else self.connection(to)
        data = {"Status": str(status), "Data": json.dumps(payload).encode("utf-8")}
        self.plugin.onMessage(conn, data)

    def connection(self, location_id):
        return next(c for c in self.fake.connections if c.Name == "openaq_%s" % location_id)

    @property
    def devices(self):
        return self.fake.devices


# ---------------------------------------------------------------------------
# Plugin manifest
# ---------------------------------------------------------------------------
class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PLUGIN_PATH, encoding="utf-8") as fh:
            source = fh.read()
        block = re.search(r"<plugin\b.*?</plugin>", source, re.S)
        cls.root = ET.fromstring(block.group(0))

    def test_plugin_key_is_stable(self):
        # Changing the key orphans every existing hardware instance in Domoticz.
        self.assertEqual(self.root.attrib["key"], "xfr_openaq4")

    def test_wikilink_points_at_repository(self):
        self.assertEqual(self.root.attrib["wikilink"],
                         "https://github.com/janreimen/Domoticz-OpenAQ-Plugin")

    def test_parameter_fields(self):
        fields = {p.attrib["field"]: p for p in self.root.find("params")}
        self.assertEqual(set(fields), {"Mode1", "Mode2", "Mode3", "Mode4", "Mode6"})
        self.assertEqual(fields["Mode1"].attrib["default"], "10")
        self.assertEqual(fields["Mode2"].attrib["required"], "true")
        self.assertEqual(fields["Mode3"].attrib["required"], "false")
        self.assertEqual(fields["Mode3"].attrib["default"], "")
        self.assertEqual(fields["Mode4"].attrib["default"], "3")
        self.assertEqual(fields["Mode4"].attrib["required"], "false")

    def test_location_field_takes_a_comma_separated_list(self):
        fields = {p.attrib["field"]: p for p in self.root.find("params")}
        self.assertIn("Location IDs", fields["Mode3"].attrib["label"])
        self.assertIn("comma separated", fields["Mode3"].attrib["label"])
        self.assertIn("blank = auto-detect", fields["Mode3"].attrib["label"])
        self.assertEqual(fields["Mode4"].attrib["label"], "Max auto-detected stations")

    def test_only_the_api_key_field_is_masked(self):
        fields = {p.attrib["field"]: p for p in self.root.find("params")}
        self.assertEqual(fields["Mode2"].attrib.get("password"), "true")
        for name in ("Mode1", "Mode3", "Mode4", "Mode6"):
            self.assertNotIn("password", fields[name].attrib)

    def test_radius_label_states_the_openaq_maximum(self):
        fields = {p.attrib["field"]: p for p in self.root.find("params")}
        self.assertIn("max 25", fields["Mode1"].attrib["label"])


# ---------------------------------------------------------------------------
# onStart
# ---------------------------------------------------------------------------
class StartupTests(PluginTestCase):
    def test_missing_api_key_aborts_before_any_request(self):
        self.mod.Parameters["Mode2"] = ""
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("API-KEY" in e for e in self.fake.error_lines))
        self.assertEqual(self.api_calls, [])
        self.assertEqual(self.devices, {})

    def test_explicit_location_id_skips_discovery(self):
        self.start_default()
        self.assertEqual(self.api_calls, ["/v3/locations/9645"])

    def test_non_numeric_location_id_aborts(self):
        self.mod.Parameters["Mode3"] = "abc"
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("is not a positive whole number" in e for e in self.fake.error_lines))
        self.assertTrue(any("No valid OpenAQ Location ID entered" in e for e in self.fake.error_lines))
        self.assertEqual(self.api_calls, [])

    def test_blank_location_id_discovers_from_domoticz_coordinates(self):
        self.mod.Parameters["Mode3"] = ""
        self.start_default()
        self.assertEqual(len(self.api_calls), 2)
        parsed = urllib.parse.urlparse(self.api_calls[0])
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/v3/locations")
        self.assertEqual(query["coordinates"], ["49.61,6.13"])
        self.assertEqual(query["radius"], ["10000"])
        # the lookup asks for a full page and ranks the results by distance itself
        self.assertEqual(query["limit"], ["100"])
        self.assertEqual(self.api_calls[1], "/v3/locations/9645")

    def test_radius_is_converted_to_metres_with_safe_default(self):
        cases = (("10", "10000"), ("25", "25000"), ("3", "3000"),
                 ("abc", "10000"), ("", "10000"), ("0", "10000"), ("-5", "10000"))
        for raw, metres in cases:
            with self.subTest(radius=raw):
                self.plugin = self.mod.BasePlugin()
                self.api_calls.clear()
                self.mod.Parameters.update(Mode1=raw, Mode3="")
                self.plugin.onStart()
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.api_calls[0]).query)
                self.assertEqual(query["radius"], [metres])

    def test_discovery_without_result_aborts(self):
        self.mod.Parameters["Mode3"] = ""
        self.search_response = {"results": []}
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("No OpenAQ location found" in e for e in self.fake.error_lines))

    def test_discovery_request_failure_aborts(self):
        self.mod.Parameters["Mode3"] = ""
        self.search_response = None
        self.assertIs(self.plugin.onStart(), False)

    def test_location_without_usable_sensors_aborts(self):
        self.location_response = location()
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("no usable sensors" in e for e in self.fake.error_lines))

    def test_location_lookup_failure_aborts(self):
        self.location_response = None
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("Could not fetch sensor list" in e for e in self.fake.error_lines))

    def test_debug_parameter_controls_domoticz_debugging(self):
        self.mod.Parameters["Mode6"] = "Debug"
        self.start_default()
        self.assertEqual(self.fake.debugging, 1)
        self.plugin = self.mod.BasePlugin()
        self.mod.Parameters["Mode6"] = "Normal"
        self.plugin.onStart()
        self.assertEqual(self.fake.debugging, 0)

    def test_creates_the_documented_devices(self):
        self.start_default()
        expected_names = {
            UNIT_BC: "BC", UNIT_CO: "CO", UNIT_NO2: "NO<sub>2</sub>",
            UNIT_O3: "O<sub>3</sub>", UNIT_PM10: "PM<sub>10</sub>",
            UNIT_PM25: "PM<sub>2.5</sub>", UNIT_SO2: "SO<sub>2</sub>",
            UNIT_INFO: "Info", UNIT_POLLUTANTS: "Pollutants",
            UNIT_AQI: "Air Quality Index",
        }
        self.assertEqual(set(self.devices), set(expected_names))
        for unit, name in expected_names.items():
            self.assertEqual(self.devices[unit].Name, name)
            self.assertEqual(self.devices[unit].Used, 1)
        for unit in range(UNIT_BC, UNIT_SO2 + 1):
            self.assertEqual(self.devices[unit].TypeName, "Custom")
            self.assertEqual(self.devices[unit].Options, {"Custom": "0;µg/m³"})
        self.assertEqual(self.devices[UNIT_INFO].TypeName, "Text")
        # Pollutants is a native Domoticz Alert device (General / Alert)
        self.assertEqual((self.devices[UNIT_POLLUTANTS].Type,
                          self.devices[UNIT_POLLUTANTS].SubType), (243, 22))
        self.assertEqual(self.devices[UNIT_AQI].Options, {"Custom": "0;"})

    def test_restart_does_not_recreate_existing_devices(self):
        self.start_default()
        self.assertEqual(len(self.fake.created_units), 10)
        self.plugin = self.mod.BasePlugin()
        self.start_default()
        self.assertEqual(len(self.fake.created_units), 10)

    def test_opens_one_https_connection_to_openaq(self):
        self.start_default()
        self.assertEqual(len(self.fake.connections), 1)
        conn = self.fake.connections[0]
        self.assertEqual(conn.Name, "openaq_9645")
        self.assertEqual((conn.Address, conn.Port, conn.Protocol),
                         ("api.openaq.org", "443", "HTTPS"))
        self.assertEqual(conn.connect_calls, 1)

    def test_icon_image_is_created_once(self):
        self.start_default()
        self.assertIn("xfr_openaq2", self.fake.images)


# ---------------------------------------------------------------------------
# Readings -> devices
# ---------------------------------------------------------------------------
class ReadingTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.start_default()

    def test_values_and_units_update_devices(self):
        self.deliver({"results": [
            reading(11, 40.0), reading(12, 20.0), reading(13, 60.0),
            reading(14, 130.0), reading(15, 12.0), reading(99, 5.0)]})
        d = self.devices
        self.assertEqual((d[UNIT_PM25].nValue, d[UNIT_PM25].sValue), (40, "40.0"))
        self.assertEqual(d[UNIT_PM10].sValue, "20.0")
        self.assertEqual(d[UNIT_NO2].sValue, "60.0")
        self.assertEqual(d[UNIT_O3].sValue, "130.0")
        self.assertEqual(d[UNIT_PM25].Options, {"Custom": "0;µg/m³"})
        # Pollutants that were not reported are left alone
        self.assertEqual(d[UNIT_BC].update_count, 0)
        self.assertEqual(d[UNIT_SO2].update_count, 0)

    def test_value_is_rounded_to_one_decimal_and_nvalue_truncated(self):
        self.deliver({"results": [reading(12, 12.34)]})
        self.assertEqual((self.devices[UNIT_PM10].nValue, self.devices[UNIT_PM10].sValue),
                         (12, "12.3"))

    def test_unit_reported_by_the_api_is_used(self):
        self.location_response = location(sensor(11, "co", "ppm"))
        self.plugin = self.mod.BasePlugin()
        self.start_default()
        conn = self.fake.connections[-1]
        self.plugin.onMessage(conn, {"Status": "200", "Data": json.dumps(
            {"results": [reading(11, 0.4)]}).encode()})
        self.assertEqual(self.devices[UNIT_CO].Options, {"Custom": "0;ppm"})

    def test_alert_level_follows_the_worst_pollutant(self):
        # PM2.5 40 > 30 and O3 130 > 120 are "Medium" (level 2); NO2 60 is level 1.
        self.deliver({"results": [
            reading(11, 40.0), reading(12, 20.0), reading(13, 60.0), reading(14, 130.0)]})
        self.assertEqual(self.devices[UNIT_POLLUTANTS].nValue, 2)
        self.assertIn("O<sub>3</sub>", self.devices[UNIT_POLLUTANTS].sValue)
        self.assertEqual(self.devices[UNIT_AQI].nValue, 2)
        self.assertEqual(self.devices[UNIT_AQI].sValue, "2")

    def test_alert_levels_cover_the_full_scale(self):
        # PM2.5 limits: 15 / 30 / 55 / 110 -> levels 0..4
        for value, level in ((10, 0), (15, 0), (16, 1), (30, 1), (31, 2),
                             (55, 2), (56, 3), (110, 3), (111, 4), (500, 4)):
            with self.subTest(pm25=value):
                self.plugin = self.mod.BasePlugin()
                self.devices.clear()
                self.fake.connections.clear()
                self.start_default()
                self.deliver({"results": [reading(11, float(value))]})
                self.assertEqual(self.devices[UNIT_AQI].nValue, level)
                self.assertEqual(self.devices[UNIT_POLLUTANTS].nValue, level)

    def test_clean_air_shows_no_alert(self):
        self.deliver({"results": [reading(11, 10.0), reading(12, 10.0)]})
        self.assertEqual(self.devices[UNIT_POLLUTANTS].nValue, 0)
        self.assertEqual(self.devices[UNIT_POLLUTANTS].sValue, "No alert")
        self.assertEqual(self.devices[UNIT_AQI].sValue, "0")

    def test_info_device_reports_location_and_reading_counts(self):
        self.deliver({"results": [
            reading(11, 40.0), reading(12, 20.0), reading(13, 60.0),
            reading(14, 130.0), reading(15, 12.0), reading(99, 5.0)]})
        self.assertEqual(
            self.devices[UNIT_INFO].sValue,
            "OpenAQ Location ID: 9645<br/>Sensors reporting: 4/6")

    def test_sentinel_and_zero_values_are_ignored(self):
        self.deliver({"results": [reading(11, -999.0), reading(12, 0.0)]})
        self.assertEqual(self.devices[UNIT_PM25].update_count, 0)
        self.assertEqual(self.devices[UNIT_PM10].update_count, 0)
        self.assertIn("Sensors reporting: 0/2", self.devices[UNIT_INFO].sValue)

    def test_incomplete_readings_are_ignored(self):
        self.deliver({"results": [
            {"sensorsId": 11, "value": None, "datetime": {"utc": "2026-10-16T05:00:00Z"}},
            {"sensorsId": 12, "value": 20.0},
            {"sensorsId": 13, "value": 20.0, "datetime": None}]})
        for unit in (UNIT_PM25, UNIT_PM10, UNIT_NO2):
            self.assertEqual(self.devices[unit].update_count, 0)

    def test_newest_reading_wins_between_sensors_of_the_same_pollutant(self):
        self.location_response = location(sensor(11, "pm25"), sensor(21, "pm25"))
        self.plugin = self.mod.BasePlugin()
        self.devices.clear()
        self.fake.connections.clear()
        self.start_default()
        self.deliver({"results": [
            reading(11, 30.0, "2026-10-16T05:00:00Z"),
            reading(21, 50.0, "2026-10-16T06:00:00Z")]})
        self.assertEqual(self.devices[UNIT_PM25].sValue, "50.0")

    def test_newer_poll_replaces_older_value(self):
        self.deliver({"results": [reading(11, 30.0, "2026-10-16T05:00:00Z")]})
        self.deliver({"results": [reading(11, 35.0, "2026-10-16T06:00:00Z")]})
        self.assertEqual(self.devices[UNIT_PM25].sValue, "35.0")

    def test_older_reading_never_replaces_a_newer_one(self):
        self.deliver({"results": [reading(11, 50.0, "2026-10-16T06:00:00Z")]})
        self.deliver({"results": [reading(11, 10.0, "2026-10-16T05:00:00Z")]})
        self.assertEqual(self.devices[UNIT_PM25].sValue, "50.0")

    def test_http_410_is_reported_as_retired_endpoint(self):
        self.deliver({}, status=410)
        self.assertTrue(any("410" in e and "retired" in e for e in self.fake.error_lines))
        self.assertEqual(self.devices[UNIT_AQI].update_count, 0)

    def test_other_http_errors_are_logged_and_do_not_raise(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.fake.error_lines.clear()
                self.deliver({}, status=status)
                self.assertTrue(any(("status: %d" % status) in e for e in self.fake.error_lines))
        self.assertEqual(self.devices[UNIT_AQI].update_count, 0)


# ---------------------------------------------------------------------------
# Recurring request and connection handling
# ---------------------------------------------------------------------------
class RequestTests(PluginTestCase):
    def setUp(self):
        super().setUp()
        self.start_default()
        self.conn = self.fake.connections[0]

    def test_request_targets_latest_endpoint_with_api_key_header(self):
        self.plugin.onConnect(self.conn, 0, "")
        self.assertEqual(len(self.conn.sent), 1)
        request = self.conn.sent[0]
        self.assertEqual(request["Verb"], "GET")
        self.assertEqual(request["URL"], "/v3/locations/9645/latest")
        self.assertEqual(request["Headers"]["X-API-Key"], "TEST-API-KEY")
        self.assertEqual(request["Headers"]["Host"], "api.openaq.org")
        self.assertEqual(request["Headers"]["Accept"], "application/json")

    def test_failed_connect_sends_nothing(self):
        self.plugin.onConnect(self.conn, 1, "connection refused")
        self.assertEqual(self.conn.sent, [])

    def test_polls_immediately_then_once_per_hour(self):
        self.conn._connected = True
        # 10 s heartbeat: one request on the first beat, the next after 360 beats
        for _ in range(361):
            self.plugin.onHeartbeat()
        self.assertEqual(len(self.conn.sent), 2)

    def test_no_request_between_hourly_polls(self):
        self.conn._connected = True
        for _ in range(360):
            self.plugin.onHeartbeat()
        self.assertEqual(len(self.conn.sent), 1)

    def test_dropped_connection_is_reopened(self):
        before = self.conn.connect_calls
        self.conn._connecting = False
        self.conn._connected = False
        self.plugin.onHeartbeat()
        self.assertEqual(self.conn.connect_calls, before + 1)
        self.assertEqual(self.conn.sent, [])


class BlockingGetTests(PluginTestCase):
    """The one-off startup lookups, with the HTTPS layer mocked."""

    PATCH_BLOCKING_GET = False

    def run_get(self, status=200, body=b'{"results": []}', exc=None):
        conn = mock.MagicMock()
        if exc is not None:
            conn.request.side_effect = exc
        conn.getresponse.return_value = types.SimpleNamespace(
            status=status, read=lambda: body)
        with mock.patch("http.client.HTTPSConnection", return_value=conn) as ctor:
            self.plugin._BasePlugin__API_KEY = "TEST-API-KEY"
            result = self.plugin._BasePlugin__blocking_get("/v3/locations/9645")
        return result, conn, ctor

    def test_success_returns_parsed_json_and_closes_connection(self):
        result, conn, _ = self.run_get(body=b'{"results": [{"id": 1}]}')
        self.assertEqual(result, {"results": [{"id": 1}]})
        conn.close.assert_called_once()

    def test_request_uses_get_and_api_key_header(self):
        _, conn, _ = self.run_get()
        args, kwargs = conn.request.call_args
        self.assertEqual(args, ("GET", "/v3/locations/9645"))
        self.assertEqual(kwargs["headers"]["X-API-Key"], "TEST-API-KEY")

    def test_tls_certificates_are_verified(self):
        _, _, ctor = self.run_get()
        args, kwargs = ctor.call_args
        self.assertEqual(args[:2], ("api.openaq.org", 443))
        self.assertEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(kwargs["context"].check_hostname)

    def test_http_error_returns_none_and_logs_status(self):
        result, conn, _ = self.run_get(status=401, body=b'{"detail": "denied"}')
        self.assertIsNone(result)
        self.assertTrue(any("HTTP 401" in e for e in self.fake.error_lines))
        conn.close.assert_called_once()

    def test_network_error_returns_none_and_closes_connection(self):
        result, conn, _ = self.run_get(exc=OSError("network unreachable"))
        self.assertIsNone(result)
        self.assertTrue(any("failed" in e for e in self.fake.error_lines))
        conn.close.assert_called_once()

    def test_invalid_json_returns_none(self):
        result, _, _ = self.run_get(body=b"<html>not json</html>")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 4.1 helpers
# ---------------------------------------------------------------------------
SECRET = "SECRET-KEY-123"
HOME_LAT, HOME_LON = "49.6116", "6.1319"
NOT_SET = object()

# The device definitions created by 4.0. Units 1-10 must stay exactly like this
# so that existing devices and their history survive an upgrade.
FROZEN_DEVICES = {
    # unit: (Name, TypeName, Type, SubType, Options)
    1: ("BC", "Custom", 0, 0, {"Custom": "0;µg/m³"}),
    2: ("CO", "Custom", 0, 0, {"Custom": "0;µg/m³"}),
    3: ("NO<sub>2</sub>", "Custom", 0, 0, {"Custom": "0;µg/m³"}),
    4: ("O<sub>3</sub>", "Custom", 0, 0, {"Custom": "0;µg/m³"}),
    5: ("PM<sub>10</sub>", "Custom", 0, 0, {"Custom": "0;µg/m³"}),
    6: ("PM<sub>2.5</sub>", "Custom", 0, 0, {"Custom": "0;µg/m³"}),
    7: ("SO<sub>2</sub>", "Custom", 0, 0, {"Custom": "0;µg/m³"}),
    8: ("Info", "Text", 0, 0, {}),
    9: ("Pollutants", None, 243, 22, {}),
    10: ("Air Quality Index", "Custom", 0, 0, {"Custom": "0;"}),
}


class TrackingSettings(dict):
    """Settings that count how often the plugin asks for the Domoticz location
    with ``Settings.get("Location")`` (config_2_log iterates the dict to mask
    it, which is deliberately not counted)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.location_reads = 0

    def get(self, key, default=None):
        if key == "Location":
            self.location_reads += 1
        return super().get(key, default)


class RestartableTestCase(PluginTestCase):
    def restart(self, params=None, settings=None, search=NOT_SET, location=NOT_SET,
                locations=None, keep_devices=False):
        """Throw all state away and start a brand-new plugin instance with the
        given hardware parameters. Returns what onStart returned. With
        ``keep_devices`` the devices of the previous run stay, as they do in
        Domoticz when the hardware is restarted."""
        for sink in (self.fake.debug_lines, self.fake.log_lines, self.fake.error_lines,
                     self.fake.connections, self.fake.created_units, self.api_calls):
            del sink[:]
        if not keep_devices:
            self.devices.clear()
        self.locations = dict(locations or {})
        self.mod.Parameters = dict(self.PARAMS, **(params or {}))
        self.mod.Settings = dict(self.SETTINGS, **(settings or {}))
        self.search_response = (
            {"results": [{"id": 9645, "name": "Test station"}]} if search is NOT_SET else search)
        self.location_response = default_location() if location is NOT_SET else location
        self.plugin = self.mod.BasePlugin()
        return self.plugin.onStart()

    def discovery_query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.api_calls[0]).query)


class HttpsTestCase(PluginTestCase):
    """Runs the plugin against a mocked ``http.client.HTTPSConnection`` so the
    real startup helper, and everything it logs, is exercised."""

    PATCH_BLOCKING_GET = False
    PARAMS = dict(PluginTestCase.PARAMS, Mode2=SECRET, Mode3="", Mode6="Debug")
    SETTINGS = {"Location": "%s;%s" % (HOME_LAT, HOME_LON), "Language": "en",
                "WebPassword": "hunter2", "ProwlAPI": "prowl-secret"}

    def setUp(self):
        super().setUp()
        self.https_requests = []
        self.https_connections = 0
        self.https_responder = self.default_https_responder
        self.https_error = None
        patcher = mock.patch("http.client.HTTPSConnection", side_effect=self._https_route)
        patcher.start()
        self.addCleanup(patcher.stop)

    def default_https_responder(self, path):
        if path.startswith("/v3/locations?"):
            return 200, json.dumps({"results": [{"id": 9645, "name": "Test station"}]}).encode()
        return 200, json.dumps(default_location()).encode()

    def _https_route(self, host, port, timeout=None, context=None):
        self.https_connections += 1
        conn = mock.MagicMock()
        state = {}

        def request(method, path, headers=None):
            if self.https_error is not None:
                raise self.https_error
            state["path"] = path
            self.https_requests.append((path, dict(headers or {})))

        def getresponse():
            status, body = self.https_responder(state["path"])
            return types.SimpleNamespace(status=status, read=lambda: body)

        conn.request.side_effect = request
        conn.getresponse.side_effect = getresponse
        return conn

    def all_log_lines(self):
        return self.fake.debug_lines + self.fake.log_lines + self.fake.error_lines


# ---------------------------------------------------------------------------
# 4.1: the API key and the Domoticz location never reach the log
# ---------------------------------------------------------------------------
class LogHygieneTests(HttpsTestCase):
    NEEDLES = (SECRET, HOME_LAT, HOME_LON, "hunter2", "prowl-secret")

    def assert_log_is_clean(self):
        lines = self.all_log_lines()
        self.assertGreater(len(lines), 2, "no log lines at all: the check would be vacuous")
        for needle in self.NEEDLES:
            self.assertEqual([l for l in lines if needle in l], [],
                             "%r reached the log" % needle)

    def test_automatic_lookup_and_a_poll_never_log_the_key_or_the_location(self):
        self.assertIs(self.plugin.onStart(), True)
        # the secrets really were in play
        path, headers = self.https_requests[0]
        self.assertIn("coordinates=%s%%2C%s" % (HOME_LAT, HOME_LON), path)
        self.assertEqual(headers["X-API-Key"], SECRET)
        self.deliver({"results": [reading(11, 40.0)]})
        self.assert_log_is_clean()

    def test_explicit_location_id_flow_is_clean(self):
        self.mod.Parameters["Mode3"] = "9645"
        self.assertIs(self.plugin.onStart(), True)
        self.assertEqual([p for p, _ in self.https_requests], ["/v3/locations/9645"])
        self.assert_log_is_clean()

    def test_lookup_that_finds_nothing_is_clean(self):
        self.https_responder = lambda path: (200, b'{"results": []}')
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("No OpenAQ location found" in e for e in self.fake.error_lines))
        self.assert_log_is_clean()

    def test_http_error_that_echoes_the_key_is_scrubbed(self):
        self.https_responder = lambda path: (
            401, ('{"detail": "bad key %s"}' % SECRET).encode())
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("<api-key>" in e for e in self.fake.error_lines))
        self.assert_log_is_clean()

    def test_network_error_that_mentions_the_key_is_scrubbed(self):
        self.https_error = OSError("cannot reach host using key %s" % SECRET)
        self.assertIs(self.plugin.onStart(), False)
        self.assertTrue(any("failed" in e for e in self.fake.error_lines))
        self.assert_log_is_clean()

    def test_poll_response_that_echoes_the_key_is_scrubbed_in_the_debug_line(self):
        self.assertIs(self.plugin.onStart(), True)
        self.deliver({"results": [], "note": SECRET})
        self.assert_log_is_clean()

    def test_request_path_is_logged_without_its_query_string(self):
        self.assertIs(self.plugin.onStart(), True)
        self.assertIn("Blocking GET /v3/locations?<query hidden> -> HTTP 200",
                      self.fake.debug_lines)
        self.assertIn("Blocking GET /v3/locations/9645 -> HTTP 200", self.fake.debug_lines)

    def test_config_dump_masks_everything_that_is_not_allow_listed(self):
        self.mod.Parameters.update(
            Username="", Password="pw-secret", Mode4="3", Mode5="", Mode7="future-secret")
        self.assertIs(self.plugin.onStart(), True)
        dump = self.fake.debug_lines
        for line in (
            "Parameter 'Mode2'...: '<masked>'",
            "Parameter 'Username'...: '<empty>'",
            "Parameter 'Password'...: '<masked>'",
            "Parameter 'Mode5'...: '<empty>'",
            "Parameter 'Mode7'...: '<masked>'",
            "Setting 'Location'...: '<masked>'",
            "Setting 'WebPassword'...: '<masked>'",
            "Setting 'ProwlAPI'...: '<masked>'",
            # harmless values stay visible, so Debug output is still useful
            "Parameter 'Mode1'...: '10'",
            "Parameter 'Mode3'...: ''",
            "Parameter 'Mode4'...: '3'",
            "Parameter 'Mode6'...: 'Debug'",
            "Setting 'Language'...: 'en'",
        ):
            self.assertIn(line, dump)
        for needle in ("pw-secret", "future-secret"):
            self.assertEqual([l for l in self.all_log_lines() if needle in l], [])


class MaskValueTests(PluginTestCase):
    def test_allow_listed_names_are_shown(self):
        loggable = self.mod.LOGGABLE_PARAMETERS
        self.assertEqual(self.mod.mask_value("Mode1", "10", loggable), "10")
        self.assertEqual(self.mod.mask_value("Mode3", "", loggable), "")

    def test_other_names_are_masked_or_marked_empty(self):
        loggable = self.mod.LOGGABLE_PARAMETERS
        for value, expected in (("abc", "<masked>"), (0, "<masked>"),
                                ("", "<empty>"), (None, "<empty>")):
            with self.subTest(value=value):
                self.assertEqual(self.mod.mask_value("Anything", value, loggable), expected)

    def test_secret_names_are_not_allow_listed(self):
        for name in ("Mode2", "Mode5", "Username", "Password",
                     "Address", "Port", "SerialPort"):
            self.assertNotIn(name, self.mod.LOGGABLE_PARAMETERS)
        self.assertNotIn("Location", self.mod.LOGGABLE_SETTINGS)

    def test_the_numeric_hardware_fields_are_shown(self):
        for name in ("Mode1", "Mode3", "Mode4", "Mode6"):
            self.assertIn(name, self.mod.LOGGABLE_PARAMETERS)


# ---------------------------------------------------------------------------
# 4.1: a failed start leaves the plugin idle
# ---------------------------------------------------------------------------
class FailedStartTests(RestartableTestCase):
    CASES = (
        ("missing API key", dict(params={"Mode2": ""})),
        ("blank API key", dict(params={"Mode2": "   "})),
        ("non-numeric location ID", dict(params={"Mode3": "abc"})),
        ("zero location ID", dict(params={"Mode3": "0"})),
        ("negative location ID", dict(params={"Mode3": "-5"})),
        ("no location found", dict(params={"Mode3": ""}, search={"results": []})),
        ("discovery request failed", dict(params={"Mode3": ""}, search=None)),
        ("discovered location without an ID",
         dict(params={"Mode3": ""}, search={"results": [{"name": "x"}]})),
        ("sensor lookup failed", dict(location=None)),
        ("no usable sensors", dict(location=location())),
        ("Domoticz location not set",
         dict(params={"Mode3": ""}, settings={"Location": ""})),
        ("Domoticz location invalid",
         dict(params={"Mode3": ""}, settings={"Location": "abc;def"})),
    )

    def test_every_failure_leaves_the_plugin_idle(self):
        for label, kwargs in self.CASES:
            with self.subTest(case=label):
                self.assertIs(self.restart(**kwargs), False)
                summary = [e for e in self.fake.error_lines if "OpenAQ plugin not started" in e]
                self.assertEqual(len(summary), 1)
                self.assertEqual(self.fake.connections, [])
                self.assertEqual(self.devices, {})
                conn = FakeConnection(Name="openaq")
                payload = {"Status": "200",
                           "Data": json.dumps({"results": [reading(11, 40.0)]}).encode()}
                # none of these may raise after a failed start
                self.plugin.onHeartbeat()
                self.plugin.onConnect(conn, 0, "")
                self.plugin.onMessage(conn, payload)
                self.plugin.onStop()
                self.assertEqual(conn.sent, [])
                self.assertEqual(self.devices, {})
                self.assertEqual(self.fake.connections, [])

    def test_a_plugin_that_never_started_can_be_stopped_and_heartbeated(self):
        plugin = self.mod.BasePlugin()
        plugin.onHeartbeat()
        plugin.onStop()
        self.assertEqual(self.fake.error_lines, [])

    def test_start_can_be_retried_on_the_same_instance_after_a_failure(self):
        self.assertIs(self.restart(location=None), False)
        self.location_response = default_location()
        self.assertIs(self.plugin.onStart(), True)
        conn = self.fake.connections[0]
        conn._connected = True
        self.plugin.onHeartbeat()
        self.assertEqual(len(conn.sent), 1)

    def test_a_failed_restart_without_onstop_leaves_the_plugin_idle(self):
        # Domoticz calls onStop before it restarts a plugin, but a failed
        # onStart must not depend on that: a stale "started" state would let
        # the heartbeat poll with the previous settings.
        self.assertIs(self.restart(), True)
        conn = self.fake.connections[0]
        conn._connected = True
        self.plugin.onHeartbeat()
        self.assertEqual(len(conn.sent), 1)
        self.mod.Parameters = dict(self.PARAMS, Mode2="")
        self.assertIs(self.plugin.onStart(), False)
        self.plugin.onHeartbeat()
        self.plugin.onConnect(conn, 0, "")
        self.deliver({"results": [reading(11, 40.0)]})
        self.assertEqual(len(conn.sent), 1)

    def test_a_stopped_plugin_ignores_further_callbacks(self):
        self.assertIs(self.restart(), True)
        conn = self.fake.connections[0]
        conn._connected = True
        self.plugin.onStop()
        self.plugin.onHeartbeat()
        self.plugin.onConnect(conn, 0, "")
        self.deliver({"results": [reading(11, 40.0)]})
        self.assertEqual(conn.sent, [])
        self.assertEqual(self.devices[UNIT_PM25].update_count, 0)


# ---------------------------------------------------------------------------
# 4.1: the Domoticz location is only read for the automatic lookup
# ---------------------------------------------------------------------------
class LazyLocationTests(RestartableTestCase):
    def test_explicit_id_never_reads_the_domoticz_location(self):
        self.mod.Settings = TrackingSettings(self.SETTINGS)
        self.start_default()
        self.assertEqual(self.mod.Settings.location_reads, 0)
        self.assertEqual(self.api_calls, ["/v3/locations/9645"])

    def test_blank_id_reads_the_domoticz_location(self):
        self.mod.Settings = TrackingSettings(self.SETTINGS)
        self.mod.Parameters["Mode3"] = ""
        self.start_default()
        self.assertGreaterEqual(self.mod.Settings.location_reads, 1)

    def test_explicit_id_starts_without_a_usable_domoticz_location(self):
        for label, settings in (("empty", {"Location": ""}), ("separator only", {"Location": ";"}),
                                ("garbage", {"Location": "abc"}), ("key missing", {})):
            with self.subTest(location=label):
                self.assertIs(self.restart(settings=settings, params={"Mode3": "9645"}), True)
                self.assertEqual(self.api_calls, ["/v3/locations/9645"])

    def test_invalid_domoticz_location_stops_the_automatic_lookup(self):
        for raw in ("", "abc;def", "49.61", "49.61;6.13;1", ";", "91;6", "49;181", "nan;6", "49;inf"):
            with self.subTest(location=raw):
                self.assertIs(self.restart(params={"Mode3": ""}, settings={"Location": raw}), False)
                self.assertEqual(self.api_calls, [])
                errors = self.fake.error_lines
                self.assertTrue(any("The Domoticz location is not set or not valid" in e for e in errors))
                if raw:
                    self.assertEqual([e for e in errors if raw in e], [], "raw value was echoed")

    def test_valid_coordinates_are_sent_rounded_to_four_decimals(self):
        for raw, expected in (
            ("49.61;6.13", "49.61,6.13"),
            (" 49.61 ; 6.13 ", "49.61,6.13"),
            ("49.611234;6.129876", "49.6112,6.1299"),
            ("-33.8688;151.2093", "-33.8688,151.2093"),
            ("90;180", "90.0,180.0"),
        ):
            with self.subTest(location=raw):
                self.assertIs(self.restart(params={"Mode3": ""}, settings={"Location": raw}), True)
                self.assertEqual(self.discovery_query()["coordinates"], [expected])


# ---------------------------------------------------------------------------
# 4.1: radius validation
# ---------------------------------------------------------------------------
class RadiusTests(RestartableTestCase):
    CASES = (
        # raw field value, metres sent to OpenAQ, fragment of the expected log line
        ("10", 10000, None),
        ("25", 25000, None),
        ("3", 3000, None),
        (" 7 ", 7000, None),
        ("2.5", 2500, None),
        ("2,5", 2500, None),
        ("0.0004", 1, None),
        ("40", 25000, "above the OpenAQ maximum of 25 km; using 25 km"),
        ("25.1", 25000, "above the OpenAQ maximum"),
        ("1e3", 25000, "above the OpenAQ maximum"),
        ("abc", 10000, "is not a positive number; using the default of 10 km"),
        ("0", 10000, "is not a positive number"),
        ("-5", 10000, "is not a positive number"),
        ("nan", 10000, "is not a positive number"),
        ("inf", 10000, "is not a positive number"),
        ("", 10000, "Radius is empty; using the default of 10 km"),
    )

    def test_radius_is_validated_and_the_value_used_is_logged(self):
        for raw, metres, fragment in self.CASES:
            with self.subTest(radius=raw):
                self.assertIs(self.restart(params={"Mode1": raw, "Mode3": ""}), True)
                self.assertEqual(self.discovery_query()["radius"], [str(metres)])
                logs = self.fake.log_lines
                if fragment is None:
                    self.assertEqual([l for l in logs if l.startswith("Radius")], [])
                else:
                    self.assertTrue(any(fragment in l for l in logs), logs)
                used = "{:g}".format(metres / 1000.0)
                self.assertIn(
                    "Looking for up to 1 OpenAQ location(s) within %s km of the Domoticz location"
                    % used, logs)

    def test_radius_is_ignored_when_a_location_id_is_entered(self):
        self.assertIs(self.restart(params={"Mode1": "abc", "Mode3": "9645"}), True)
        self.assertEqual(self.api_calls, ["/v3/locations/9645"])
        self.assertEqual([l for l in self.fake.log_lines if "adius" in l], [])


# ---------------------------------------------------------------------------
# 4.1: other configuration handling and wording
# ---------------------------------------------------------------------------
class ConfigurationTests(RestartableTestCase):
    def test_start_reports_success(self):
        self.assertIs(self.plugin.onStart(), True)

    def test_api_key_whitespace_is_trimmed(self):
        self.mod.Parameters["Mode2"] = "  TEST-API-KEY \n"
        self.start_default()
        conn = self.fake.connections[0]
        self.plugin.onConnect(conn, 0, "")
        self.assertEqual(conn.sent[0]["Headers"]["X-API-Key"], "TEST-API-KEY")

    def test_location_id_whitespace_is_trimmed(self):
        self.assertIs(self.restart(params={"Mode3": " 9645 "}), True)
        self.assertEqual(self.api_calls, ["/v3/locations/9645"])

    def test_zero_and_negative_location_ids_are_rejected_clearly(self):
        for raw in ("0", "-5"):
            with self.subTest(location_id=raw):
                self.assertIs(self.restart(params={"Mode3": raw}), False)
                self.assertTrue(any("is not a positive whole number and is ignored" in e
                                    for e in self.fake.error_lines))
                self.assertEqual(self.api_calls, [])


class AutoDetectWordingTests(RestartableTestCase):
    """One auto-detected station: what the log tells the user."""

    def search_with_distances(self):
        return {"results": [found(9645, "Test station", 49.62, 6.13)]}

    def test_message_does_not_claim_more_than_the_ranking_shows(self):
        self.assertIs(self.restart(params={"Mode3": ""}, search=self.search_with_distances()), True)
        lines = self.fake.log_lines
        self.assertIn("OpenAQ returned 1 location(s); the nearest ones are used.", lines)
        self.assertIn("Auto-detected OpenAQ location 1 of 1: 'Test station' (id 9645)", lines)
        self.assertEqual([l for l in lines if "Auto-selected" in l], [])

    def test_message_admits_it_when_the_order_is_unknown(self):
        self.assertIs(self.restart(params={"Mode3": ""}), True)  # no coordinates, no distance
        self.assertTrue(any("without usable distances" in l and "not necessarily the nearest" in l
                            for l in self.fake.log_lines))

    def test_message_tells_the_user_which_id_to_enter(self):
        self.assertIs(self.restart(params={"Mode3": ""}), True)
        self.assertIn(
            "To keep using the auto-detected location(s), enter 9645 in the "
            "'OpenAQ Location IDs' hardware field.", self.fake.log_lines)

    def test_nothing_is_announced_when_the_id_was_entered(self):
        self.assertIs(self.restart(), True)
        self.assertEqual([l for l in self.fake.log_lines if "Auto-detected" in l], [])
        self.assertEqual(self.fake.log_lines, [])


# ---------------------------------------------------------------------------
# 4.1: devices 1-10 are unchanged, so an upgrade keeps devices and history
# ---------------------------------------------------------------------------
class UpgradeCompatibilityTests(PluginTestCase):
    def test_units_one_to_ten_are_exactly_the_4_0_definitions(self):
        self.start_default()
        self.assertEqual(sorted(self.devices), list(range(1, 11)))
        image = self.fake.images["xfr_openaq2"].ID
        for unit, expected in FROZEN_DEVICES.items():
            dev = self.devices[unit]
            self.assertEqual((dev.Name, dev.TypeName, dev.Type, dev.SubType, dev.Options),
                             expected, "unit %d changed" % unit)
            self.assertEqual(dev.Used, 1)
            self.assertEqual(dev.Image, None if unit in (8, 9) else image)

    def test_devices_created_by_4_0_are_reused_and_keep_their_identity(self):
        self.fake.images["xfr_openaq2"] = types.SimpleNamespace(ID=7)
        existing = {}
        for unit, (name, type_name, dev_type, sub_type, options) in FROZEN_DEVICES.items():
            dev = FakeDevice(self.fake, Unit=unit, Name=name, TypeName=type_name, Type=dev_type,
                             Subtype=sub_type, Options=options, Used=1)
            self.devices[unit] = dev
            existing[unit] = dev
        self.start_default()
        self.assertEqual(self.fake.created_units, [])
        self.deliver({"results": [reading(11, 40.0), reading(12, 20.0)]})
        for unit, dev in existing.items():
            self.assertIs(self.devices[unit], dev)
            self.assertEqual(dev.Name, FROZEN_DEVICES[unit][0])
        self.assertEqual(self.devices[UNIT_PM25].sValue, "40.0")
        self.assertEqual(self.devices[UNIT_PM10].sValue, "20.0")
        self.assertGreater(self.devices[UNIT_AQI].update_count, 0)


# ---------------------------------------------------------------------------
# 4.2: several stations
# ---------------------------------------------------------------------------
HOME = (49.61, 6.13)  # the Domoticz location of RestartableTestCase
NEAR, MID, FAR = (49.62, 6.13), (49.65, 6.13), (49.70, 6.13)  # about 1, 4.5 and 10 km away


def fill_blocks(testcase, ids, **kwargs):
    """Start the plugin with these Location IDs and return what onStart returned."""
    return testcase.restart(params={"Mode3": ",".join(str(i) for i in ids)}, **kwargs)


class MultiStationTests(RestartableTestCase):
    IDS = (9645, 1140)

    def setUp(self):
        super().setUp()
        self.assertIs(fill_blocks(self, self.IDS), True, self.fake.error_lines)

    def test_every_station_gets_its_own_block_of_ten_units(self):
        self.assertEqual(sorted(self.devices), list(range(1, 21)))
        self.assertEqual(self.devices[UNIT_PM25].Name, "PM<sub>2.5</sub> (Test station)")
        self.assertEqual(self.devices[UNIT_PM25 + 10].Name, "PM<sub>2.5</sub> (Station 1140)")
        self.assertEqual(self.devices[UNIT_INFO].Name, "Info (Test station)")
        self.assertEqual(self.devices[UNIT_AQI + 10].Name, "Air Quality Index (Station 1140)")

    def test_the_second_block_has_the_same_layout_as_the_first(self):
        image = self.fake.images["xfr_openaq2"].ID
        for unit, (_name, type_name, dev_type, sub_type, options) in FROZEN_DEVICES.items():
            for dev in (self.devices[unit], self.devices[unit + 10]):
                self.assertEqual((dev.TypeName, dev.Type, dev.SubType, dev.Options),
                                 (type_name, dev_type, sub_type, options))
                self.assertEqual(dev.Used, 1)
                self.assertEqual(dev.Image, None if unit in (8, 9) else image)

    def test_devices_are_tagged_with_their_location_id(self):
        for local in range(1, 11):
            self.assertEqual(self.devices[local].DeviceID, "OAQ9645-%d" % local)
            self.assertEqual(self.devices[local + 10].DeviceID, "OAQ1140-%d" % local)

    def test_one_connection_per_station(self):
        self.assertEqual([c.Name for c in self.fake.connections], ["openaq_9645", "openaq_1140"])
        for conn in self.fake.connections:
            self.assertEqual((conn.Address, conn.Port, conn.Protocol),
                             ("api.openaq.org", "443", "HTTPS"))
            self.assertEqual(conn.connect_calls, 1)

    def test_every_station_is_polled_at_its_own_url(self):
        for conn in self.fake.connections:
            conn._connected = True
        self.plugin.onHeartbeat()
        self.assertEqual([c.sent[0]["URL"] for c in self.fake.connections],
                         ["/v3/locations/9645/latest", "/v3/locations/1140/latest"])
        for conn in self.fake.connections:
            self.assertEqual(conn.sent[0]["Headers"]["X-API-Key"], "TEST-API-KEY")
            self.plugin.onConnect(conn, 0, "")
            self.assertEqual(len(conn.sent), 2)

    def test_a_dropped_connection_of_one_station_is_reopened_alone(self):
        first, second = self.fake.connections
        first._connected = True
        second._connecting = second._connected = False
        self.plugin.onHeartbeat()
        self.assertEqual(len(first.sent), 1)
        self.assertEqual((second.connect_calls, second.sent), (2, []))

    def test_readings_never_mix_between_stations(self):
        self.deliver({"results": [reading(11, 40.0), reading(12, 20.0)]}, to=1140)
        d = self.devices
        self.assertEqual((d[UNIT_PM25 + 10].sValue, d[UNIT_PM10 + 10].sValue), ("40.0", "20.0"))
        for local in range(1, 8):
            self.assertEqual(d[local].update_count, 0)
        self.deliver({"results": [reading(11, 12.0)]}, to=9645)
        self.assertEqual(d[UNIT_PM25].sValue, "12.0")
        self.assertEqual(d[UNIT_PM25 + 10].sValue, "40.0")

    def test_info_alert_and_index_are_kept_per_station(self):
        self.deliver({"results": [reading(11, 40.0)]}, to=9645)   # level 2
        self.deliver({"results": [reading(11, 10.0)]}, to=1140)   # level 0
        d = self.devices
        self.assertEqual(d[UNIT_INFO].sValue, "OpenAQ Location ID: 9645<br/>Sensors reporting: 1/1")
        self.assertEqual(d[UNIT_INFO + 10].sValue, "OpenAQ Location ID: 1140<br/>Sensors reporting: 1/1")
        self.assertEqual((d[UNIT_AQI].nValue, d[UNIT_AQI + 10].nValue), (2, 0))
        self.assertEqual(d[UNIT_POLLUTANTS + 10].sValue, "No alert")
        self.assertIn("PM<sub>2.5</sub>", d[UNIT_POLLUTANTS].sValue)

    def test_a_newer_reading_of_one_station_does_not_replace_another_stations_value(self):
        self.deliver({"results": [reading(11, 50.0, "2026-10-16T06:00:00Z")]}, to=9645)
        self.deliver({"results": [reading(11, 10.0, "2026-10-16T05:00:00Z")]}, to=1140)
        self.assertEqual(self.devices[UNIT_PM25].sValue, "50.0")
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "10.0")

    def test_status_errors_name_the_connection_of_the_station(self):
        self.deliver({}, status=429, to=1140)
        self.deliver({}, status=410, to=9645)
        self.assertIn("openaq_1140 returned a status: 429", self.fake.error_lines)
        self.assertTrue(any(e.startswith("openaq_9645 returned HTTP 410 Gone")
                            for e in self.fake.error_lines))

    def test_a_message_from_an_unknown_connection_is_ignored(self):
        stranger = FakeConnection(Name="openaq_5")
        self.plugin.onMessage(stranger, {"Status": "200", "Data": b'{"results": []}'})
        self.plugin.onConnect(stranger, 0, "")
        self.assertEqual(stranger.sent, [])
        self.assertEqual(self.fake.error_lines, [])

    def test_stopping_forgets_the_readings_of_every_station(self):
        self.deliver({"results": [reading(11, 40.0)]}, to=9645)
        self.deliver({"results": [reading(11, 30.0)]}, to=1140)
        self.plugin.onStop()
        self.assertIs(self.restart(keep_devices=True, params={"Mode3": "9645,1140"}), True)
        # a restart begins empty: an older reading is accepted again
        self.deliver({"results": [reading(11, 5.0, "2000-01-01T00:00:00Z")]}, to=1140)
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "5.0")

    def test_the_log_stays_quiet_when_all_stations_start(self):
        self.assertEqual(self.fake.log_lines, [])
        self.assertEqual(self.fake.error_lines, [])


class LocationIdListTests(RestartableTestCase):
    def lookups(self):
        return [int(p.rsplit("/", 1)[1]) for p in self.api_calls]

    def test_valid_lists(self):
        cases = (
            ("9645", [9645]),
            ("9645,1140", [9645, 1140]),
            ("1140,9645", [1140, 9645]),
            (" 9645 , 1140 ", [9645, 1140]),
            ("9645,1140,", [9645, 1140]),
            (",,9645,,1140", [9645, 1140]),
            ("9645,1140,2088", [9645, 1140, 2088]),
            ("007", [7]),
        )
        for raw, expected in cases:
            with self.subTest(field=raw):
                self.assertIs(fill_blocks(self, [raw]), True, self.fake.error_lines)
                self.assertEqual(self.lookups(), expected)
                self.assertEqual(self.fake.error_lines, [])
                self.assertEqual(len(self.devices), 10 * len(expected))

    def test_a_duplicate_is_used_once(self):
        self.assertIs(fill_blocks(self, ["9645,9645,1140,9645"]), True)
        self.assertEqual(self.lookups(), [9645, 1140])
        self.assertEqual(len(self.devices), 20)
        self.assertEqual(self.fake.log_lines.count(
            "OpenAQ Location ID 9645 is listed more than once; using it once"), 2)

    def test_invalid_entries_are_ignored_and_reported_while_the_rest_runs(self):
        for bad in ("abc", "0", "-5", "1_000", "+5", "12.5", "1140;2088", "0x10",
                    "٣", "1234567890123", "9 6"):
            with self.subTest(entry=bad):
                self.assertIs(fill_blocks(self, ["9645,%s,1140" % bad]), True)
                self.assertEqual(self.lookups(), [9645, 1140])
                self.assertEqual(self.fake.error_lines,
                                 ["OpenAQ Location ID '%s' is not a positive whole number "
                                  "and is ignored" % bad])

    def test_a_field_without_a_valid_id_does_not_start(self):
        for raw in ("abc", "0", "-5, 0", ",,", " , ", "abc,def"):
            with self.subTest(field=raw):
                self.assertIs(fill_blocks(self, [raw]), False)
                self.assertTrue(any("No valid OpenAQ Location ID entered" in e
                                    for e in self.fake.error_lines))
                self.assertEqual(self.api_calls, [])
                self.assertEqual(self.devices, {})
                self.assertEqual(self.fake.connections, [])

    def test_a_blank_field_is_not_a_list(self):
        for raw in ("", "   "):
            with self.subTest(field=repr(raw)):
                self.assertIs(self.restart(params={"Mode3": raw}), True)
                self.assertTrue(self.api_calls[0].startswith("/v3/locations?"))

    def test_more_than_twenty_ids_are_capped(self):
        ids = list(range(1001, 1026))
        self.assertIs(fill_blocks(self, ids), True)
        self.assertEqual(self.lookups(), ids[:20])
        self.assertEqual(sorted(self.devices), list(range(1, 201)))
        self.assertIn("Too many OpenAQ Location IDs (25 entered); only the first 20 are used",
                      self.fake.error_lines)

    def test_entries_are_not_echoed_with_the_api_key(self):
        self.assertIs(fill_blocks(self, ["9645,TEST-API-KEY"]), True)
        self.assertEqual(self.fake.error_lines,
                         ["OpenAQ Location ID '<api-key>' is not a positive whole number "
                          "and is ignored"])
        self.assertEqual([l for l in self.fake.error_lines if "TEST-API-KEY" in l], [])

    def test_long_entries_are_shortened(self):
        self.assertIs(fill_blocks(self, ["9645," + "x" * 200]), True)
        self.assertEqual(self.fake.error_lines[0].count("x"), 30)


class StableBlockTests(RestartableTestCase):
    """The core of 4.2: a station always finds its own devices again."""

    def setUp(self):
        super().setUp()
        self.assertIs(fill_blocks(self, [9645, 1140]), True)
        # a poll of both, so that the Info devices name their stations
        self.deliver({"results": [reading(11, 30.0)]}, to=9645)
        self.deliver({"results": [reading(11, 55.0)]}, to=1140)

    def again(self, ids, **kwargs):
        return fill_blocks(self, ids, keep_devices=True, **kwargs)

    def test_a_restart_changes_nothing(self):
        before = dict(self.devices)
        self.assertIs(self.again([9645, 1140]), True)
        self.assertEqual(self.fake.created_units, [])
        self.assertEqual(self.devices, before)

    def test_reordering_the_list_keeps_every_station_on_its_own_units(self):
        self.assertIs(self.again([1140, 9645]), True)
        self.assertEqual(self.fake.created_units, [])
        self.deliver({"results": [reading(11, 61.0)]}, to=1140)
        self.deliver({"results": [reading(11, 17.0)]}, to=9645)
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "61.0")
        self.assertEqual(self.devices[UNIT_PM25].sValue, "17.0")
        self.assertEqual(self.devices[UNIT_INFO].sValue.split("<br/>")[0], "OpenAQ Location ID: 9645")

    def test_a_temporary_failure_does_not_move_the_other_station(self):
        self.assertIs(self.again([9645, 1140], locations={9645: None}), True)
        self.assertEqual(self.fake.created_units, [])
        self.assertTrue(any("OpenAQ started with 1 of 2 stations. Not configured: 9645."
                            in e for e in self.fake.error_lines))
        self.assertEqual([c.Name for c in self.fake.connections], ["openaq_1140"])
        self.deliver({"results": [reading(11, 70.0)]}, to=1140)
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "70.0")
        self.assertEqual(self.devices[UNIT_PM25].sValue, "30.0")  # untouched

    def test_a_station_that_failed_on_the_first_start_takes_its_own_slot_later(self):
        self.assertIs(fill_blocks(self, [9645, 1140], locations={9645: None}), True)
        self.assertEqual(sorted(self.devices), list(range(11, 21)))   # only 1140
        self.assertIs(self.again([9645, 1140]), True)
        self.assertEqual(sorted(self.fake.created_units), list(range(1, 11)))
        self.assertEqual(self.devices[UNIT_PM25].DeviceID, "OAQ9645-6")
        self.assertEqual(self.devices[UNIT_PM25 + 10].DeviceID, "OAQ1140-6")

    def test_removing_the_first_id_leaves_the_other_station_where_it_is(self):
        names = {u: d.Name for u, d in self.devices.items()}
        self.assertIs(self.again([1140]), True)
        self.assertEqual(self.fake.created_units, [])
        self.deliver({"results": [reading(11, 80.0)]}, to=1140)
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "80.0")
        self.assertEqual({u: d.Name for u, d in self.devices.items()}, names)  # no renaming
        self.assertTrue(any(l.startswith("Units 1-10 belong to OpenAQ location 9645, which is "
                                         "not in use now.") for l in self.fake.log_lines))

    def test_a_new_id_takes_the_lowest_free_block(self):
        self.assertIs(self.again([2088, 9645, 1140]), True)
        self.assertEqual(sorted(self.fake.created_units), list(range(21, 31)))
        self.assertEqual(self.devices[21 + 5].DeviceID, "OAQ2088-6")
        self.assertEqual(self.devices[21 + 5].Name, "PM<sub>2.5</sub> (Station 2088)")

    def test_the_slot_of_a_removed_station_stays_reserved_for_it(self):
        self.assertIs(self.again([2088, 1140]), True)            # 9645 removed, 2088 added
        self.assertEqual(sorted(self.fake.created_units), list(range(21, 31)))
        self.assertIs(self.again([9645, 2088, 1140]), True)      # 9645 comes back
        self.assertEqual(self.fake.created_units, [])
        self.deliver({"results": [reading(11, 22.0)]}, to=9645)
        self.assertEqual(self.devices[UNIT_PM25].sValue, "22.0")

    def test_deleting_the_devices_of_a_station_frees_its_slot(self):
        for unit in range(1, 11):
            del self.devices[unit]
        self.assertIs(self.again([2088, 1140]), True)
        self.assertEqual(sorted(self.fake.created_units), list(range(1, 11)))
        self.assertEqual(self.devices[UNIT_PM25].DeviceID, "OAQ2088-6")

    def test_a_station_is_recognised_by_its_info_text_when_the_tags_are_gone(self):
        for unit, dev in self.devices.items():
            dev.DeviceID = "%08X" % unit          # what devices without a tag look like
        self.assertIs(self.again([1140, 9645]), True)
        self.assertEqual(self.fake.created_units, [])
        self.deliver({"results": [reading(11, 44.0)]}, to=1140)
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "44.0")
        self.assertEqual(self.devices[UNIT_PM25].sValue, "30.0")

    def test_a_tag_wins_over_the_info_text(self):
        self.devices[UNIT_INFO].sValue = "OpenAQ Location ID: 1140<br/>Sensors reporting: 1/1"
        self.assertIs(self.again([1140, 9645]), True)
        self.assertEqual(self.fake.created_units, [])
        self.deliver({"results": [reading(11, 44.0)]}, to=9645)
        self.assertEqual(self.devices[UNIT_PM25].sValue, "44.0")

    def test_a_tag_that_does_not_fit_its_unit_is_not_believed(self):
        # a device moved or renamed by hand: OAQ555-6 sitting on local unit 3
        self.devices[3].DeviceID = "OAQ555-6"
        self.devices[UNIT_PM25].DeviceID = "OAQ555-1"
        for unit in (1, 2, 4, 5, 7, 9, 10):
            self.devices[unit].DeviceID = "%08X" % unit
        self.assertIs(self.again([555, 9645, 1140]), True)
        # block 0 still belongs to 9645 (its Info device says so); 555 is new
        self.assertEqual(sorted(self.fake.created_units), list(range(21, 31)))
        self.deliver({"results": [reading(11, 44.0)]}, to=9645)
        self.assertEqual(self.devices[UNIT_PM25].sValue, "44.0")

    def test_all_twenty_slots_in_use_means_no_twenty_first_station(self):
        ids = list(range(1001, 1021))
        self.assertIs(fill_blocks(self, ids), True)
        for i in ids:
            self.deliver({"results": [reading(11, 30.0)]}, to=i)
        self.assertIs(self.again([5000]), False)
        self.assertTrue(any("No free slot for OpenAQ location 5000" in e
                            for e in self.fake.error_lines))
        self.assertEqual(self.fake.created_units, [])
        self.assertIs(self.again([5000, 1001]), True)             # the others still run
        self.assertEqual([c.Name for c in self.fake.connections], ["openaq_1001"])
        self.assertTrue(any("Not configured: 5000" in e for e in self.fake.error_lines))

    def test_nineteen_stations_leave_room_for_one_more(self):
        ids = list(range(1001, 1020))
        self.assertIs(fill_blocks(self, ids), True)
        for i in ids:
            self.deliver({"results": [reading(11, 30.0)]}, to=i)
        self.assertIs(self.again(ids + [5000]), True)
        self.assertEqual(sorted(self.fake.created_units), list(range(191, 201)))


def legacy_devices(testcase, info="OpenAQ Location ID: 9645<br/>Sensors reporting: 4/6"):
    """The ten devices 4.0 and 4.1 created: no tag, the plain 4.0 names. ``info`` is what
    their Info device shows (None: it was never updated)."""
    testcase.devices.clear()
    testcase.fake.images["xfr_openaq2"] = types.SimpleNamespace(ID=7)
    made = {}
    for unit, (name, type_name, dev_type, sub_type, options) in FROZEN_DEVICES.items():
        made[unit] = FakeDevice(testcase.fake, Unit=unit, Name=name, TypeName=type_name,
                                Type=dev_type, Subtype=sub_type, Options=options, Used=1)
        testcase.devices[unit] = made[unit]
    if info is not None:
        made[UNIT_INFO].sValue = info
    return made


class LegacyDeviceTests(RestartableTestCase):
    """Units 1-10 created by 4.0 and 4.1 carry no tag: the Info device names their station."""

    def legacy(self, info="OpenAQ Location ID: 9645<br/>Sensors reporting: 4/6"):
        return legacy_devices(self, info)

    def start(self, ids):
        return fill_blocks(self, ids, keep_devices=True)

    def test_the_station_named_by_the_info_device_keeps_units_one_to_ten(self):
        made = self.legacy()
        self.assertIs(self.start([1140, 9645]), True)
        self.assertEqual(sorted(self.fake.created_units), list(range(11, 21)))
        for unit, dev in made.items():
            self.assertIs(self.devices[unit], dev)
            self.assertEqual(dev.Name, FROZEN_DEVICES[unit][0])   # never renamed
        self.deliver({"results": [reading(11, 40.0)]}, to=9645)
        self.deliver({"results": [reading(11, 25.0)]}, to=1140)
        self.assertEqual(made[UNIT_PM25].sValue, "40.0")
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "25.0")
        self.assertEqual(self.devices[UNIT_PM25 + 10].DeviceID, "OAQ1140-6")

    def test_a_station_that_is_not_the_legacy_one_never_gets_its_devices(self):
        made = self.legacy(info="OpenAQ Location ID: 1140<br/>Sensors reporting: 4/6")
        self.assertIs(self.start([9645]), True)           # the user now enters another ID
        self.assertEqual(sorted(self.fake.created_units), list(range(11, 21)))
        self.deliver({"results": [reading(11, 40.0)]}, to=9645)
        self.assertEqual(made[UNIT_PM25].update_count, 0)
        self.assertEqual(self.devices[UNIT_PM25 + 10].sValue, "40.0")
        self.assertTrue(any("Units 1-10 belong to OpenAQ location 1140" in l
                            for l in self.fake.log_lines))

    def test_legacy_devices_that_were_never_updated_go_to_the_first_station(self):
        made = self.legacy(info=None)
        self.assertIs(self.start([1140, 9645]), True)
        self.assertEqual(sorted(self.fake.created_units), list(range(11, 21)))
        self.deliver({"results": [reading(11, 40.0)]}, to=1140)
        self.assertEqual(made[UNIT_PM25].sValue, "40.0")

    def test_an_info_device_in_any_other_state_is_not_an_owner(self):
        for info in ("", "Sensors reporting: 4/6", "OpenAQ Location ID: x"):
            with self.subTest(info=info):
                made = self.legacy(info=info)
                self.assertIs(self.start([9645]), True)
                self.assertEqual(self.fake.created_units, [])
                self.deliver({"results": [reading(11, 40.0)]})
                self.assertEqual(made[UNIT_PM25].sValue, "40.0")

    def test_a_missing_legacy_device_is_created_with_a_tag_and_no_suffix(self):
        self.legacy()
        del self.devices[UNIT_AQI]
        self.assertIs(self.start([9645]), True)
        self.assertEqual(self.fake.created_units, [UNIT_AQI])
        self.assertEqual(self.devices[UNIT_AQI].Name, "Air Quality Index")
        self.assertEqual(self.devices[UNIT_AQI].DeviceID, "OAQ9645-10")

    def test_one_entered_id_works_on_legacy_devices_as_before(self):
        made = self.legacy()
        self.assertIs(self.start([9645]), True)
        self.assertEqual(self.fake.created_units, [])
        self.deliver({"results": [reading(11, 40.0), reading(12, 20.0)]})
        self.assertEqual((made[UNIT_PM25].sValue, made[UNIT_PM10].sValue), ("40.0", "20.0"))
        self.assertEqual(made[UNIT_INFO].sValue, "OpenAQ Location ID: 9645<br/>Sensors reporting: 2/2")


class AutoDetectStationsTests(RestartableTestCase):
    def coords_search(self):
        # out of order on purpose: FAR (id 3), NEAR (id 1), MID (id 2)
        return {"results": [found(3, "Far", *FAR), found(1, "Near", *NEAR), found(2, "Mid", *MID)]}

    def lookups(self):
        return [int(p.rsplit("/", 1)[1]) for p in self.api_calls[1:]]

    def auto(self, count="3", search=None, **kwargs):
        return self.restart(params={"Mode3": "", "Mode4": count},
                            search=search if search is not None else self.coords_search(), **kwargs)

    def test_the_nearest_locations_are_used_nearest_first(self):
        self.assertIs(self.auto("3"), True)
        self.assertEqual(self.lookups(), [1, 2, 3])
        self.assertEqual(self.devices[UNIT_PM25].DeviceID, "OAQ1-6")
        self.assertEqual(self.devices[UNIT_PM25 + 10].DeviceID, "OAQ2-6")
        self.assertEqual(self.devices[UNIT_PM25 + 20].DeviceID, "OAQ3-6")

    def test_only_as_many_as_asked_for(self):
        for count, expected in (("1", [1]), ("2", [1, 2]), ("3", [1, 2, 3]), ("10", [1, 2, 3])):
            with self.subTest(count=count):
                self.assertIs(self.auto(count), True)
                self.assertEqual(self.lookups(), expected)
                self.assertEqual(len(self.devices), 10 * len(expected))

    def test_the_lookup_asks_for_a_full_page(self):
        self.auto()
        self.assertEqual(self.discovery_query()["limit"], ["100"])

    def test_the_log_says_what_was_chosen_and_which_ids_to_enter(self):
        self.auto("2")
        self.assertEqual(self.fake.log_lines, [
            "Looking for up to 2 OpenAQ location(s) within 10 km of the Domoticz location",
            "OpenAQ returned 3 location(s); the nearest ones are used.",
            "Auto-detected OpenAQ location 1 of 2: 'Near' (id 1)",
            "Auto-detected OpenAQ location 2 of 2: 'Mid' (id 2)",
            "To keep using the auto-detected location(s), enter 1,2 in the "
            "'OpenAQ Location IDs' hardware field.",
        ])

    def test_without_coordinates_the_distance_field_of_openaq_is_used(self):
        search = {"results": [found(3, distance=30), found(1, distance=10), found(2, distance=20.5)]}
        self.assertIs(self.auto("2", search=search), True)
        self.assertEqual(self.lookups(), [1, 2])
        self.assertIn("OpenAQ returned 3 location(s); the nearest ones are used.", self.fake.log_lines)

    def test_coordinates_and_the_distance_field_are_never_mixed(self):
        # one location has only a distance: use the distance of all, or none
        search = {"results": [found(3, "Far", *FAR), found(1, distance=1), found(2, "Mid", *MID)]}
        self.assertIs(self.auto("2", search=search), True)
        self.assertEqual(self.lookups(), [3, 1])       # OpenAQ's order, no ranking
        self.assertTrue(any("without usable distances" in l for l in self.fake.log_lines))

    def test_some_coordinates_missing_means_the_distance_field_of_all_is_used(self):
        search = {"results": [dict(found(3, "Far", *FAR), distance=30),
                              found(1, distance=10),
                              dict(found(2, "Mid", *MID), distance=20)]}
        self.assertIs(self.auto("2", search=search), True)
        self.assertEqual(self.lookups(), [1, 2])
        self.assertIn("OpenAQ returned 3 location(s); the nearest ones are used.", self.fake.log_lines)

    def test_without_any_distance_the_order_of_openaq_is_kept_and_said_so(self):
        search = {"results": [found(3), found(1), found(2)]}
        self.assertIs(self.auto("2", search=search), True)
        self.assertEqual(self.lookups(), [3, 1])
        self.assertIn("OpenAQ returned 3 location(s) without usable distances; the first ones "
                      "returned are used, which are not necessarily the nearest.",
                      self.fake.log_lines)

    def test_unusable_coordinates_fall_back_to_the_order_of_openaq(self):
        for bad in ({"latitude": "x", "longitude": 6.1}, {"latitude": 91, "longitude": 6.1},
                    {"latitude": float("nan"), "longitude": 6.1}, {"latitude": True, "longitude": 6.1},
                    {"latitude": 49.6}, "49.6,6.1", None):
            with self.subTest(coordinates=bad):
                near = found(1, "Near", *NEAR)
                broken = dict(found(2, "Far", *FAR), coordinates=bad)
                self.assertIs(self.auto("1", search={"results": [broken, near]}), True)
                self.assertEqual(self.lookups(), [2])

    def test_results_without_a_usable_id_are_skipped_and_duplicates_dropped(self):
        search = {"results": [{"name": "no id"}, found(True), found("7"), found(0), found(-3),
                              found(1, "Mid", *MID), found(1, "Near", *NEAR), found(2, "Far", *FAR), "junk", None]}
        self.assertIs(self.auto("5", search=search), True)
        self.assertEqual(self.lookups(), [1, 2])
        self.assertTrue(any("OpenAQ returned 2 location(s)" in l for l in self.fake.log_lines))

    def test_a_result_without_any_usable_id_is_an_error(self):
        self.assertIs(self.auto("3", search={"results": [{"name": "x"}, found(True)]}), False)
        self.assertIn("OpenAQ returned a location without an ID", self.fake.error_lines)

    def test_max_auto_detected_stations_is_validated(self):
        many = {"results": [found(100 + i, "S%d" % i, HOME[0] + i / 1000.0, HOME[1])
                            for i in range(1, 26)]}
        cases = (
            # field, stations used, fragment of the log line
            ("3", 3, None), ("1", 1, None), (" 2 ", 2, None), ("20", 20, None), ("007", 7, None),
            ("", 1, "Max auto-detected stations is empty; using 1."),
            ("   ", 1, "Max auto-detected stations is empty; using 1."),
            ("abc", 3, "'abc' is not a positive whole number; using the default of 3"),
            ("0", 3, "'0' is not a positive whole number; using the default of 3"),
            ("-1", 3, "'-1' is not a positive whole number; using the default of 3"),
            ("2.5", 3, "'2.5' is not a positive whole number; using the default of 3"),
            ("1e2", 3, "'1e2' is not a positive whole number"),
            ("21", 20, "Max auto-detected stations 21 is above the maximum of 20; using 20"),
            ("999", 20, "above the maximum of 20"),
        )
        for raw, used, fragment in cases:
            with self.subTest(field=raw):
                self.assertIs(self.restart(params={"Mode3": "", "Mode4": raw}, search=many), True)
                self.assertEqual(len(self.api_calls) - 1, used)
                lines = [l for l in self.fake.log_lines if l.startswith("Max auto-detected")]
                if fragment is None:
                    self.assertEqual(lines, [])
                else:
                    self.assertEqual(len(lines), 1)
                    self.assertIn(fragment, lines[0])

    def test_a_field_that_was_never_saved_means_one_station_like_4_1(self):
        self.assertNotIn("Mode4", self.PARAMS)
        self.assertIs(self.restart(params={"Mode3": ""}, search=self.coords_search()), True)
        self.assertEqual(self.lookups(), [1])

    def test_the_maximum_is_ignored_when_ids_are_entered(self):
        self.assertIs(self.restart(params={"Mode3": "9645", "Mode4": "abc"}), True)
        self.assertEqual(self.api_calls, ["/v3/locations/9645"])
        self.assertEqual(self.fake.log_lines, [])

    def test_a_full_page_is_reported(self):
        page = {"results": [found(100 + i, None, *NEAR) for i in range(100)]}
        self.assertIs(self.auto("2", search=page), True)
        self.assertTrue(any(l.startswith("OpenAQ returned its maximum of 100 locations")
                            for l in self.fake.log_lines))
        self.assertIs(self.auto("2", search={"results": page["results"][:99]}), True)
        self.assertEqual([l for l in self.fake.log_lines if "maximum of 100" in l], [])

    def test_names_in_the_log_are_cleaned(self):
        search = {"results": [found(1, "<b>Bad</b>\nname", *NEAR)]}
        self.assertIs(self.auto("1", search=search), True)
        line = [l for l in self.fake.log_lines if l.startswith("Auto-detected")][0]
        self.assertNotIn("<", line)
        self.assertNotIn(">", line)

    # stations that already have devices come first ------------------------------
    def test_stations_that_already_have_devices_are_kept_first(self):
        self.assertIs(self.auto("3"), True)                    # ids 1, 2, 3
        for i in (1, 2, 3):
            self.deliver({"results": [reading(11, 30.0)]}, to=i)
        closer = found(4, "Newcomer", 49.6101, 6.13)           # nearer than all of them
        search = {"results": [closer] + self.coords_search()["results"]}
        self.assertIs(self.auto("3", search=search, keep_devices=True), True)
        self.assertEqual(self.lookups(), [1, 2, 3])
        self.assertEqual(self.fake.created_units, [])
        kept = [l for l in self.fake.log_lines if l.startswith("Auto-detected")]
        self.assertEqual(len(kept), 3)
        for line in kept:
            self.assertTrue(line.endswith(", kept because it already has devices"), line)
        # room for more: the newcomer takes the next free block
        self.assertIs(self.auto("4", search=search, keep_devices=True), True)
        self.assertEqual(self.lookups(), [1, 2, 3, 4])
        self.assertEqual(sorted(self.fake.created_units), list(range(31, 41)))

    def test_a_smaller_maximum_keeps_the_nearest_of_the_stations_in_use(self):
        self.assertIs(self.auto("3"), True)
        for i in (1, 2, 3):
            self.deliver({"results": [reading(11, 30.0)]}, to=i)
        self.assertIs(self.auto("1", keep_devices=True), True)
        self.assertEqual(self.lookups(), [1])
        self.assertTrue(any(l.startswith("Units 11-20 belong to OpenAQ location 2")
                            for l in self.fake.log_lines))
        self.assertTrue(any(l.startswith("Units 21-30 belong to OpenAQ location 3")
                            for l in self.fake.log_lines))


class NamingTests(RestartableTestCase):
    def names(self, unit=UNIT_PM25):
        return self.devices[unit].Name

    def test_a_single_station_keeps_the_names_of_4_1(self):
        self.assertIs(fill_blocks(self, [9645]), True)
        self.assertEqual({u: d.Name for u, d in self.devices.items()},
                         {u: v[0] for u, v in FROZEN_DEVICES.items()})

    def test_a_single_auto_detected_station_keeps_the_names_of_4_1(self):
        self.assertIs(self.restart(params={"Mode3": "", "Mode4": "3"}), True)
        self.assertEqual(self.names(), "PM<sub>2.5</sub>")

    def test_several_stations_add_the_station_to_every_name(self):
        self.assertIs(fill_blocks(self, [9645, 1140]), True)
        for local, (name, *_rest) in FROZEN_DEVICES.items():
            self.assertEqual(self.devices[local].Name, "%s (Test station)" % name)
            self.assertEqual(self.devices[local + 10].Name, "%s (Station 1140)" % name)

    def test_the_names_depend_on_how_many_ids_are_configured_not_on_how_many_started(self):
        self.assertIs(fill_blocks(self, [9645, 1140], locations={9645: None}), True)
        self.assertEqual(self.names(UNIT_PM25 + 10), "PM<sub>2.5</sub> (Station 1140)")

    def test_stations_with_the_same_name_are_told_apart_by_their_id(self):
        same = {9645: station_location(9645, "Esch"), 1140: station_location(1140, "esch"),
                2088: station_location(2088, "Beckerich")}
        self.assertIs(fill_blocks(self, [9645, 1140, 2088], locations=same), True)
        self.assertEqual(self.names(), "PM<sub>2.5</sub> (Esch #9645)")
        self.assertEqual(self.names(UNIT_PM25 + 10), "PM<sub>2.5</sub> (esch #1140)")
        self.assertEqual(self.names(UNIT_PM25 + 20), "PM<sub>2.5</sub> (Beckerich)")

    def test_station_names_are_cleaned_and_shortened(self):
        cases = (
            ("<b>Evil</b>\x00 name", None),
            ("A" * 100, "A" * 40),
            ("  spaced \n\t out  ", "spaced out"),
            ("", "Location 1140"),
            (None, "Location 1140"),
            ("<>", "Location 1140"),
        )
        for raw, expected in cases:
            with self.subTest(name=raw):
                payload = station_location(1140, raw)
                self.assertIs(fill_blocks(self, [9645, 1140], locations={1140: payload}), True)
                suffix = self.names(UNIT_PM25 + 10).replace("PM<sub>2.5</sub> (", "", 1)[:-1]
                self.assertNotIn("<", suffix)
                self.assertNotIn(">", suffix)
                self.assertLessEqual(len(suffix), 40)
                if expected is not None:
                    self.assertEqual(suffix, expected)

    def test_names_that_cannot_clash_with_markup_helpers(self):
        self.assertEqual(self.mod.clean_name("<script>alert(1)</script>", 5),
                         "script alert(1) /script")
        self.assertEqual(self.mod.clean_name(12345, 5), "12345")


class HelperTests(PluginTestCase):
    def test_haversine_distance(self):
        d = self.mod.haversine_km
        self.assertAlmostEqual(d(49.61, 6.13, 49.61, 6.13), 0.0)
        self.assertAlmostEqual(d(49.61, 6.13, 49.62, 6.13), 1.112, places=2)
        self.assertAlmostEqual(d(0, 0, 0, 180), 20015.1, delta=1)
        self.assertAlmostEqual(d(49.61, 6.13, 48.85, 2.35), d(48.85, 2.35, 49.61, 6.13))

    def test_ranking_is_stable_for_equal_distances(self):
        a, b = found(1, "a", *NEAR), found(2, "b", *NEAR)
        self.assertEqual([l["id"] for l in self.mod.rank_by_distance([b, a], *HOME)[0]], [2, 1])

    def test_ranking_never_mixes_computed_and_reported_distances(self):
        rank = self.mod.rank_by_distance
        locs = [dict(found(3, "Far", *FAR), distance=1), found(1, distance=5),
                dict(found(2, "Mid", *MID), distance=3)]
        ordered, ranked = rank(locs, *HOME)
        self.assertTrue(ranked)
        self.assertEqual([l["id"] for l in ordered], [3, 2, 1])        # by the field, not geometry
        ordered, ranked = rank([found(3, "Far", *FAR), found(1, distance=5)], *HOME)
        self.assertFalse(ranked)                                         # nothing usable for one
        self.assertEqual([l["id"] for l in ordered], [3, 1])

    def test_ranking_without_locations(self):
        self.assertEqual(self.mod.rank_by_distance([], *HOME), ([], False))

    def test_location_ids_from_openaq(self):
        valid = self.mod.is_valid_location_id
        for good in (1, 9645, 10 ** 12 - 1):
            self.assertTrue(valid(good))
        for bad in (0, -1, True, False, "5", 5.0, None, 10 ** 12):
            self.assertFalse(valid(bad), repr(bad))


class PartialFailureTests(HttpsTestCase):
    """A station that cannot be set up is skipped and reported; the rest runs."""

    PARAMS = dict(HttpsTestCase.PARAMS, Mode3="9645,1140,2088")

    def respond(self, per_id):
        def responder(path):
            match = re.match(r"^/v3/locations/(\d+)$", path)
            handler = per_id.get(int(match.group(1))) if match else None
            if handler is not None:
                return handler
            payload = station_location(int(match.group(1)), "S%s" % match.group(1))
            return 200, json.dumps(payload).encode()
        self.https_responder = responder

    def test_one_failing_station_does_not_stop_the_others(self):
        self.respond({1140: (404, b'{"detail": "not found"}')})
        self.assertIs(self.plugin.onStart(), True)
        self.assertEqual([c.Name for c in self.fake.connections], ["openaq_9645", "openaq_2088"])
        self.assertEqual(sorted(self.devices), list(range(1, 11)) + list(range(21, 31)))
        self.assertTrue(any("OpenAQ started with 2 of 3 stations. Not configured: 1140. "
                            "They are not retried until the hardware is restarted or updated."
                            in e for e in self.fake.error_lines))
        self.assertTrue(any("HTTP 404" in e for e in self.fake.error_lines))

    def test_several_failing_stations_are_all_listed(self):
        self.respond({9645: (500, b"x"), 2088: (200, b'{"results": []}')})
        self.assertIs(self.plugin.onStart(), True)
        self.assertTrue(any("started with 1 of 3 stations. Not configured: 9645, 2088." in e
                            for e in self.fake.error_lines))

    def test_stations_without_usable_sensors_are_reported(self):
        self.respond({1140: (200, json.dumps(station_location(1140, "Empty", [])).encode())})
        self.assertIs(self.plugin.onStart(), True)
        self.assertIn("Location 1140 returned no usable sensors", self.fake.error_lines)

    def test_invalid_json_skips_that_station_only(self):
        self.respond({9645: (200, b"<html>")})
        self.assertIs(self.plugin.onStart(), True)
        self.assertEqual(len(self.fake.connections), 2)
        self.assertEqual(self.https_connections, 3)

    def test_when_every_station_fails_the_plugin_does_not_start(self):
        self.https_responder = lambda path: (404, b"{}")
        self.assertIs(self.plugin.onStart(), False)
        self.assertEqual(len([e for e in self.fake.error_lines
                              if "OpenAQ plugin not started" in e]), 1)
        self.assertEqual((self.fake.connections, self.devices), ([], {}))
        self.assertEqual(self.https_connections, 3)

    def test_an_unreachable_openaq_is_tried_once_not_once_per_station(self):
        self.https_error = OSError("network unreachable")
        self.assertIs(self.plugin.onStart(), False)
        self.assertEqual(self.https_connections, 1)
        self.assertIn("Skipping the remaining 2 OpenAQ location(s): OpenAQ could not be reached.",
                      self.fake.error_lines)

    def test_a_rejected_api_key_is_tried_once(self):
        self.https_responder = lambda path: (401, b'{"detail": "bad key"}')
        self.assertIs(self.plugin.onStart(), False)
        self.assertEqual(self.https_connections, 1)
        self.assertIn("Skipping the remaining 2 OpenAQ location(s): OpenAQ rejected the API "
                      "key (HTTP 401).", self.fake.error_lines)

    def test_the_rate_limit_stops_the_lookups_but_keeps_what_was_set_up(self):
        self.respond({1140: (429, b'{"detail": "slow down"}')})
        self.assertIs(self.plugin.onStart(), True)
        self.assertEqual([c.Name for c in self.fake.connections], ["openaq_9645"])
        self.assertEqual(self.https_connections, 2)
        self.assertIn("Skipping the remaining 1 OpenAQ location(s): the OpenAQ rate limit was "
                      "reached.", self.fake.error_lines)
        self.assertTrue(any("started with 1 of 3 stations. Not configured: 1140, 2088." in e
                            for e in self.fake.error_lines))

    def test_other_http_errors_do_not_stop_the_lookups(self):
        for status in (404, 500, 502):
            with self.subTest(status=status):
                del self.fake.error_lines[:]
                self.https_connections = 0
                self.respond({9645: (status, b"x")})
                self.assertIs(self.plugin.onStart(), True)
                self.assertEqual(self.https_connections, 3)
                self.assertEqual([e for e in self.fake.error_lines if "Skipping" in e], [])

    def test_the_key_never_reaches_the_log_when_stations_fail(self):
        self.respond({1140: (401, ("bad key %s" % SECRET).encode())})
        self.plugin.onStart()
        self.assertEqual([l for l in self.all_log_lines() if SECRET in l], [])


class UpgradeFrom41Tests(RestartableTestCase):
    """What a user of 4.1 sees after the upgrade."""

    def legacy(self, owner=9645):
        return legacy_devices(
            self, "OpenAQ Location ID: %d<br/>Sensors reporting: 4/6" % owner)

    def test_an_auto_detect_installation_keeps_its_station_and_adds_no_devices(self):
        made = self.legacy()
        # the nearest station is another one: 4.1 had chosen 9645 and so it stays
        search = {"results": [found(1140, "Nearer", *NEAR), found(9645, "Test station", *FAR)]}
        # Mode4 does not exist yet in an instance created by 4.1
        self.assertIs(self.restart(params={"Mode3": ""}, search=search, keep_devices=True), True)
        self.assertEqual(self.fake.created_units, [])
        self.assertTrue(any("Max auto-detected stations is empty; using 1." in l
                            for l in self.fake.log_lines))
        self.assertTrue(any("(id 9645), kept because it already has devices" in l
                            for l in self.fake.log_lines))
        self.assertEqual([c.Name for c in self.fake.connections], ["openaq_9645"])
        self.deliver({"results": [reading(11, 40.0)]})
        self.assertEqual(made[UNIT_PM25].sValue, "40.0")

    def test_saving_the_hardware_page_offers_three_stations(self):
        made = self.legacy()
        search = {"results": [found(1140, "Nearer", *NEAR), found(9645, "Test station", *FAR),
                              found(2088, "Mid", *MID)]}
        self.assertIs(self.restart(params={"Mode3": "", "Mode4": "3"}, search=search,
                                   keep_devices=True), True)
        self.assertEqual(self.api_calls[1:], ["/v3/locations/9645", "/v3/locations/1140",
                                              "/v3/locations/2088"])
        self.assertEqual(sorted(self.fake.created_units), list(range(11, 31)))
        for unit, dev in made.items():
            self.assertIs(self.devices[unit], dev)
            self.assertEqual(dev.Name, FROZEN_DEVICES[unit][0])

    def test_an_installation_with_one_entered_id_changes_nothing(self):
        made = self.legacy()
        self.assertIs(self.restart(params={"Mode3": "9645"}, keep_devices=True), True)
        self.assertEqual(self.fake.created_units, [])
        self.assertEqual(self.fake.log_lines, [])
        self.assertEqual(self.api_calls, ["/v3/locations/9645"])
        for unit, dev in made.items():
            self.assertIs(self.devices[unit], dev)

    def test_the_old_single_id_format_is_still_a_valid_list(self):
        self.legacy()
        for raw in ("9645", " 9645 ", "9645,"):
            with self.subTest(field=raw):
                self.assertIs(self.restart(params={"Mode3": raw}, keep_devices=True), True)
                self.assertEqual(self.fake.created_units, [])
                self.assertEqual(self.fake.error_lines, [])


class MultiStationLogHygieneTests(HttpsTestCase):
    PARAMS = dict(HttpsTestCase.PARAMS, Mode4="3")

    def responder(self, path):
        if path.startswith("/v3/locations?"):
            results = [found(1, "Near", *NEAR), found(2, "Mid", *MID), found(3, "Far", *FAR)]
            return 200, json.dumps({"results": results}).encode()
        match = re.match(r"^/v3/locations/(\d+)$", path)
        return 200, json.dumps(station_location(int(match.group(1)), "S")).encode()

    def test_auto_detect_of_several_stations_never_logs_the_key_or_the_location(self):
        self.https_responder = self.responder
        self.assertIs(self.plugin.onStart(), True)
        for i in (1, 2, 3):
            self.deliver({"results": [reading(11, 40.0)], "echo": SECRET}, to=i)
        lines = self.all_log_lines()
        self.assertGreater(len(lines), 10)
        for needle in (SECRET, HOME_LAT, HOME_LON, "hunter2", "prowl-secret"):
            self.assertEqual([l for l in lines if needle in l], [], needle)
        self.assertIn("Blocking GET /v3/locations?<query hidden> -> HTTP 200", self.fake.debug_lines)

    def test_every_poll_carries_the_key_of_the_hardware(self):
        self.https_responder = self.responder
        self.assertIs(self.plugin.onStart(), True)
        for conn in self.fake.connections:
            self.plugin.onConnect(conn, 0, "")
            self.assertEqual(conn.sent[0]["Headers"]["X-API-Key"], SECRET)


# ---------------------------------------------------------------------------
# Repository standards (documentation and hygiene)
# ---------------------------------------------------------------------------
def read(relative_path):
    with open(os.path.join(HERE, relative_path), encoding="utf-8") as fh:
        return fh.read()


class RepositoryStandardsTests(unittest.TestCase):
    REQUIRED_FILES = (
        "plugin.py", "xfr_openaq2.zip", "README.md", "LICENSE", "SECURITY.md",
        "CHANGELOG.md", "CONTRIBUTING.md", "DEPLOY.md", "ROADMAP.md",
        "requirements.txt", ".gitignore",
        ".github/CODE_OF_CONDUCT.md", ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
    )

    def test_required_files_exist(self):
        missing = [f for f in self.REQUIRED_FILES if not os.path.isfile(os.path.join(HERE, f))]
        self.assertEqual(missing, [])

    def test_plugin_uses_only_the_standard_library(self):
        stdlib = getattr(sys, "stdlib_module_names", None)
        if stdlib is None:
            self.skipTest("sys.stdlib_module_names needs Python 3.10+")
        tree = ast.parse(read("plugin.py"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - set(stdlib) - {"Domoticz"}, set())

    def test_requirements_file_declares_no_dependencies(self):
        lines = [l.strip() for l in read("requirements.txt").splitlines()]
        self.assertEqual([l for l in lines if l and not l.startswith("#")], [])

    def test_version_is_shown_consistently_in_the_docs(self):
        version = re.search(r'version="([^"]+)"', read("plugin.py")).group(1)
        self.assertIn("**Version %s**" % version, read("README.md"))
        self.assertIn("## [%s]" % version, read("CHANGELOG.md"))
        self.assertIn(version, read("SECURITY.md"))
        self.assertIn(version, read("ROADMAP.md"))

    def test_support_policy_is_stated_in_the_docs(self):
        # 4.2 is the current release, 4.1 stays supported, and everything
        # before 4.2 is announced to lose support in a future release.
        for name in ("README.md", "SECURITY.md", "ROADMAP.md"):
            text = read(name)
            self.assertIn("before 4.2", text, name)
            self.assertIn("4.1", text, name)
            self.assertRegex(text, r"(?i)will end in a future release", name)
        security = read("SECURITY.md")
        for row in (r"\| 4\.2\.x \|[^\n]*Supported", r"\| 4\.1\.x \|[^\n]*Supported"):
            self.assertRegex(security, row)
        for name in ("4.0.x", "< 4.0"):
            line = [l for l in security.splitlines() if l.startswith("| " + name)]
            self.assertTrue(line, name)
            self.assertIn("Not supported", line[0])
        self.assertIn("before 4.2", read("DEPLOY.md"))

    def test_gitignore_does_not_hide_runtime_assets(self):
        patterns = [l.strip() for l in read(".gitignore").splitlines()
                    if l.strip() and not l.startswith("#")]
        self.assertNotIn("*.zip", patterns)
        self.assertNotIn("xfr_openaq2.zip", patterns)
        self.assertNotIn("images/", patterns)

    def test_gitignore_blocks_secrets(self):
        patterns = read(".gitignore")
        for entry in (".env", "*.key", "*.pem"):
            self.assertIn(entry, patterns)

    def test_issue_templates_are_github_issue_forms(self):
        for name in ("bug_report.yml", "feature_request.yml"):
            text = read(".github/ISSUE_TEMPLATE/" + name)
            self.assertTrue(text.startswith("name:"), name)
            self.assertIn("\nbody:\n", text, name)
        self.assertIn("blank_issues_enabled: false", read(".github/ISSUE_TEMPLATE/config.yml"))

    def test_hardware_labels_in_the_docs_match_the_manifest(self):
        block = re.search(r"<plugin\b.*?</plugin>", read("plugin.py"), re.S).group(0)
        labels = [p.attrib["label"] for p in ET.fromstring(block).find("params")]
        self.assertEqual(len(labels), 5)
        for doc in ("README.md", "DEPLOY.md"):
            text = read(doc)
            for label in labels:
                self.assertIn("| %s |" % label, text, "%s does not document %r" % (doc, label))


class DocumentedMessageTests(unittest.TestCase):
    """Every log message quoted in the troubleshooting tables must still exist
    in plugin.py, so the documentation cannot drift away from the code."""

    NUL = "\x00"

    @classmethod
    def normalise(cls, text):
        text = re.sub(r"\{[^}]*\}", cls.NUL, text)         # str.format fields in the code
        text = re.sub(r"<[^>]*>", cls.NUL, text)           # <placeholders> in the docs
        return re.sub(r"\d+(?:\.\d+)?", cls.NUL, text)     # numbers (limits, HTTP codes)

    @classmethod
    def plugin_strings(cls):
        tree = ast.parse(read("plugin.py"))
        return [cls.normalise(node.value) for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]

    @staticmethod
    def documented_messages():
        found = []
        for name in ("README.md", "DEPLOY.md"):
            for line in read(name).splitlines():
                match = re.match(r"^\|\s*`([^`]+)`", line)  # first quoted text of the first cell
                if match:
                    found.append((name, match.group(1)))
        return found

    def test_documented_log_messages_exist_in_the_plugin(self):
        strings = self.plugin_strings()
        messages = self.documented_messages()
        self.assertGreaterEqual(len(messages), 10)
        missing = []
        for doc, message in messages:
            # the connection name ("openaq_<id>") starts these messages
            text = re.sub(r"^openaq_<id> (?=returned)", "<connection> ", message)
            text = self.normalise(text.replace("...", "<more>"))
            pieces = [p.strip() for p in text.split(self.NUL) if len(p.strip()) >= 5]
            if not pieces or not any(all(p in s for p in pieces) for s in strings):
                missing.append((doc, message))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
