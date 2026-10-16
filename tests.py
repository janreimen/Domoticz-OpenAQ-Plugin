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
                 Options=None, Image=None, Used=0, **kwargs):
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
        self.DeviceID = "%08X" % Unit
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
        if re.match(r"^/v3/locations/\d+$", path):
            return self.location_response
        raise AssertionError("unexpected API path: %s" % path)

    # helpers --------------------------------------------------------------
    def start_default(self):
        result = self.plugin.onStart()
        self.assertIsNot(result, False, "onStart unexpectedly aborted: %s"
                         % self.fake.error_lines)
        return result

    def deliver(self, payload, status=200):
        conn = self.fake.connections[0]
        data = {"Status": str(status), "Data": json.dumps(payload).encode("utf-8")}
        self.plugin.onMessage(conn, data)

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
        self.assertEqual(set(fields), {"Mode1", "Mode2", "Mode3", "Mode6"})
        self.assertEqual(fields["Mode1"].attrib["default"], "10")
        self.assertEqual(fields["Mode2"].attrib["required"], "true")
        self.assertEqual(fields["Mode3"].attrib["required"], "false")


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
        self.assertTrue(any("must be numeric" in e for e in self.fake.error_lines))
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
        self.assertEqual(query["limit"], ["1"])
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
        self.assertEqual(conn.Name, "openaq")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
