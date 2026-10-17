#!/usr/bin/env python3
"""Offline failure contracts against actual patched upstream coordinator methods."""

import argparse
import ast
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import importlib.util
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
import uuid

SPEC = importlib.util.spec_from_file_location("prepare_nws", Path(__file__).with_name("prepare_nws.py"))
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
SOURCE = None


class UpdateFailed(Exception):
    pass


class Response:
    def __init__(self, data=None, status=200, error=None, delay=0):
        self.data, self.status, self.error, self.delay = data, status, error, delay
        self.reason = "Synthetic response"
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def json(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.data


class Session:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, **_):
        self.urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def alert(identifier="one"):
    return {"id": "https://example.invalid/alerts/" + identifier, "properties": {
        "event": "Synthetic Test", "parameters": {}, "messageType": "Alert",
        "eventCode": {"NationalWeatherService": ["TST"]}, "status": "Test",
        "severity": "Unknown", "certainty": "Unknown", "sent": "2026-10-16T00:00:00Z",
        "onset": None, "expires": "2026-10-17T00:00:00Z", "ends": None,
        "areaDesc": "Synthetic test area", "description": "Offline fixture", "instruction": None,
    }}


class NWSFailureContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SOURCE is None:
            raise unittest.SkipTest("Run with --source <verified public upstream checkout>")
        cls.tmp = tempfile.TemporaryDirectory(prefix="edsys-nws-offline-tests-")
        cls.output = Path(cls.tmp.name) / "prepared"
        result = PREPARE.prepare(SOURCE, cls.output)
        cls.package = Path(result["prepared_component"])
        tree = ast.parse((cls.package / "coordinator.py").read_text())
        original = next(item for item in tree.body if isinstance(item, ast.ClassDef))
        body = [item for item in original.body if isinstance(item, ast.AsyncFunctionDef)]
        offline = ast.ClassDef(name="Coordinator", bases=[], keywords=[], body=body, decorator_list=[])
        logger = SimpleNamespace(debug=lambda *_: None, warning=lambda *_: None)
        namespace = {"timeout": asyncio.timeout, "datetime": datetime, "hashlib": hashlib,
                     "uuid": uuid, "Any": Any, "UpdateFailed": UpdateFailed, "_LOGGER": logger,
                     "CONF_TRACKER": "tracker", "CONF_GPS_LOC": "gps_loc", "CONF_ZONE_ID": "zone_id",
                     "API_ENDPOINT": "https://api.weather.gov"}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[offline], type_ignores=[])),
                     str(cls.package / "coordinator.py"), "exec"), namespace)
        cls.coordinator_class = namespace["Coordinator"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def coordinator(self, response, config=None, timeout=1):
        instance = self.coordinator_class()
        instance._config = SimpleNamespace(data={"gps_loc": "1,2"} if config is None else config)
        instance._session = Session(response)
        instance._user_agent = "offline-fixture"
        instance.timeout = timeout
        instance.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: None))
        instance.data = {"state": 1, "alerts": [{"ID": "retained"}], "last_updated": "previous-success"}
        return instance

    def assert_failed_without_replacing_previous_data(self, coordinator):
        previous = deepcopy(coordinator.data)
        with self.assertRaises(UpdateFailed):
            asyncio.run(coordinator._async_update_data())
        self.assertEqual(coordinator.data, previous)

    def test_valid_empty_collection_is_a_real_zero(self):
        response = Response({"features": []})
        coordinator = self.coordinator(response)
        data = asyncio.run(coordinator._async_update_data())
        self.assertEqual((data["state"], data["alerts"]), (0, []))
        self.assertTrue(data["last_updated"])
        self.assertTrue(response.closed)
        self.assertEqual(coordinator._session.urls, ["https://api.weather.gov/alerts/active?point=1,2"])

    def test_valid_alert_details_are_preserved(self):
        data = asyncio.run(self.coordinator(Response({"features": [alert()]}))._async_update_data())
        self.assertEqual(data["state"], 1)
        self.assertEqual(data["alerts"][0]["Event"], "Synthetic Test")
        self.assertEqual(data["alerts"][0]["Headline"], "Synthetic Test")
        self.assertIsNone(data["alerts"][0]["Instruction"])

    def test_missing_or_invalid_feature_collection_is_not_zero(self):
        for payload in [None, [], "wrong shape", {}, {"features": None}, {"features": {}}, {"features": ""}]:
            with self.subTest(payload=payload):
                self.assert_failed_without_replacing_previous_data(self.coordinator(Response(payload)))

    def test_invalid_alert_does_not_publish_a_partial_subset(self):
        for broken in [None, {}, {"properties": {}}, {"id": None, "properties": {}},
                       {"id": "test", "properties": {"event": "Test", "parameters": {"NWSheadline": []}}}]:
            with self.subTest(broken=broken):
                self.assert_failed_without_replacing_previous_data(self.coordinator(Response({"features": [alert(), broken]})))

    def test_http_failures_are_unavailable_and_release_response(self):
        for status in [204, 401, 429, 500, 503]:
            with self.subTest(status=status):
                response = Response({"features": []}, status=status)
                self.assert_failed_without_replacing_previous_data(self.coordinator(response))
                self.assertTrue(response.closed)

    def test_attribute_transport_json_and_timeout_errors_are_not_zero(self):
        for response in [AttributeError("synthetic attribute failure"), OSError("synthetic transport failure"),
                         Response(error=ValueError("synthetic invalid JSON")),
                         Response({"features": []}, delay=0.05)]:
            with self.subTest(response=type(response).__name__):
                self.assert_failed_without_replacing_previous_data(self.coordinator(response, timeout=0.005))

    def test_missing_tracker_or_location_is_unavailable(self):
        for config in [{"tracker": "device_tracker.synthetic"}, {}]:
            with self.subTest(config=config):
                coordinator = self.coordinator(Response({"features": []}), config=config)
                self.assert_failed_without_replacing_previous_data(coordinator)
                self.assertEqual(coordinator._session.urls, [])

    def test_preparation_refuses_source_drift_and_existing_output(self):
        with tempfile.TemporaryDirectory(prefix="edsys-nws-drift-test-") as temporary:
            altered = Path(temporary) / "altered"
            shutil.copytree(Path(SOURCE) / PREPARE.COMPONENT, altered / PREPARE.COMPONENT)
            path = altered / PREPARE.COMPONENT / "coordinator.py"
            path.write_text(path.read_text() + "\n# source drift\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                PREPARE.prepare(altered, Path(temporary) / "rejected")
        with self.assertRaises(FileExistsError):
            PREPARE.prepare(SOURCE, self.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SOURCE = args.source
    unittest.main(argv=[__file__, *remaining])
