#!/usr/bin/env python3
"""Offline contract tests against actual patched upstream methods; no HA imports."""

import argparse
import ast
import asyncio
from datetime import datetime, timezone
from http import HTTPStatus
import importlib.util
from pathlib import Path
import shutil
import tempfile
import traceback
from types import SimpleNamespace
from typing import cast
import unittest

SPEC = importlib.util.spec_from_file_location("prepare_wunderground", Path(__file__).with_name("prepare_wunderground.py"))
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
SOURCE = None
FAKE_KEY = "SYNTHETIC_TEST_KEY_DO_NOT_USE"
CURRENT = {"observations": [{"stationID": "TEST", "lat": 1, "lon": 2}]}


class ClientError(Exception):
    pass


class ClientResponseError(ClientError):
    def __init__(self, status):
        self.status = status
        super().__init__("https://example.invalid/?apiKey=" + FAKE_KEY)


class AuthError(Exception):
    pass


class UpdateError(Exception):
    pass


class ApiError(Exception):
    pass


class KeyErrorFlow(ApiError):
    pass


class StationError(ApiError):
    pass


class Response:
    def __init__(self, status=200, data=None, error=None):
        self.status, self.data, self.error = status, data, error
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def json(self):
        if self.error:
            raise self.error
        return self.data


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **_):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Logger:
    def __init__(self):
        self.messages = []

    def error(self, message, *args):
        self.messages.append(message % args if args else message)


def methods(path, class_name, names, globals_, base=object):
    """Execute selected original method bodies with only explicit offline stubs."""
    tree = ast.parse(path.read_text())
    original = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == class_name)
    body = [item for item in original.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in names]
    cls = ast.ClassDef(name=class_name, bases=[ast.Name(id="OfflineBase", ctx=ast.Load())], keywords=[], body=body, decorator_list=[])
    namespace = {**globals_, "OfflineBase": base}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), "exec"), namespace)
    return namespace[class_name]


class PatchContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SOURCE is None:
            raise unittest.SkipTest("Run this file with --source <verified upstream checkout>")
        cls.tmp = tempfile.TemporaryDirectory(prefix="edsys-wupws-offline-tests-")
        cls.output = Path(cls.tmp.name) / "prepared"
        cls.prepared = PREPARE.prepare(SOURCE, cls.output)
        cls.package = Path(cls.prepared["prepared_component"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.logger = Logger()
        self.namespace = {
            "timeout": asyncio.timeout, "DEFAULT_TIMEOUT": 1,
            "ClientResponseError": ClientResponseError, "ConfigEntryAuthFailed": AuthError,
            "UpdateFailed": UpdateError, "aiohttp": SimpleNamespace(ClientError=ClientError),
            "_LOGGER": self.logger, "FIELD_OBSERVATIONS": "observations",
            "FIELD_LATITUDE": "lat", "FIELD_LONGITUDE": "lon",
        }

    def coordinator(self, session):
        path = self.package / "coordinator.py"
        namespace = dict(self.namespace)
        for item in ast.parse(path.read_text()).body:
            if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id.startswith("_RESOURCE") for target in item.targets):
                namespace[item.targets[0].id] = ast.literal_eval(item.value)
        cls = methods(path, "WundergroundPWSUpdateCoordinator", {"get_weather", "_async_request_json", "_build_url", "_check_errors"}, namespace)
        result = cls()
        result.__dict__.update(_session=session, _api_key=FAKE_KEY, _pws_id="TEST", _numeric_precision="none", _unit_system_api="e", _lang="en-US", _latitude=1, _longitude=2, data=CURRENT)
        return result

    def assert_safe_failure(self, awaitable, error_type, expected=None):
        try:
            asyncio.run(awaitable)
        except error_type as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertNotIn(FAKE_KEY, rendered)
            self.assertNotIn(FAKE_KEY, "\n".join(self.logger.messages))
            if expected:
                self.assertIn(expected, str(error))
        else:
            self.fail("Expected a sanitized failure")

    def test_http_status_authentication_and_response_release(self):
        for status, error in [(401, AuthError), (403, UpdateError), (429, UpdateError), (500, UpdateError), (204, UpdateError)]:
            with self.subTest(status=status):
                response = Response(status, {"errors": [{"message": FAKE_KEY}]})
                self.assert_safe_failure(self.coordinator(Session(response)).get_weather(), error, str(status))
                self.assertTrue(response.closed)
        self.assert_safe_failure(self.coordinator(Session(Response(data=CURRENT), Response(401))).get_weather(), AuthError, "401")

    def test_transport_json_and_provider_failures_never_echo_credentials(self):
        errors = [ClientError(FAKE_KEY), TimeoutError(FAKE_KEY)]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.assert_safe_failure(self.coordinator(Session(error)).get_weather(), UpdateError, type(error).__name__)
        for error in [ClientResponseError(200), ValueError(FAKE_KEY)]:
            self.assert_safe_failure(self.coordinator(Session(Response(error=error))).get_weather(), UpdateError, type(error).__name__)
        for payload in [None, [], "bad JSON shape", {"observations": []}, {"errors": [{"message": FAKE_KEY}, {"message": FAKE_KEY}]}]:
            self.assert_safe_failure(self.coordinator(Session(Response(data=payload))).get_weather(), UpdateError)

    def test_success_preserves_data_and_still_fetches_both_endpoints(self):
        responses = [Response(data=CURRENT), Response(data={"daypart": []})]
        session = Session(*responses)
        result = asyncio.run(self.coordinator(session).get_weather())
        self.assertEqual(result["observations"], CURRENT["observations"])
        self.assertEqual(len(session.urls), 2)
        self.assertTrue(all(response.closed for response in responses))

    def test_config_flow_failures_are_safe_and_classified(self):
        for response, error_type in [(Response(401), KeyErrorFlow), (Response(204), StationError), (Response(500), ApiError), (Response(error=ClientResponseError(200)), ApiError), (Response(error=ValueError(FAKE_KEY)), ApiError), (Response(data={"observations": []}), ApiError), (ClientError(FAKE_KEY), ApiError)]:
            with self.subTest(error=error_type.__name__):
                session = Session(response)
                namespace = {**self.namespace, "HTTPStatus": HTTPStatus, "CONF_API_KEY": "api_key", "CONF_PWS_ID": "pws_id", "InvalidApiKeyError": KeyErrorFlow, "InvalidStationIdError": StationError, "InvalidApiResponseError": ApiError, "async_create_clientsession": lambda _: session}
                cls = methods(self.package / "config_flow.py", "WundergroundPWSFlowHandler", {"_validate_user_input"}, namespace)
                flow = cls()
                flow.hass = object()
                self.assert_safe_failure(flow._validate_user_input({"api_key": FAKE_KEY, "pws_id": "TEST"}), error_type)

    def test_missing_optional_values_remain_unknown_and_zero_is_valid(self):
        tree = ast.parse((self.package / "wupws_obs_sensors.py").read_text())
        checked = set()
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "WundergroundPWSSensorEntityDescription":
                continue
            fields = {item.arg: item.value for item in call.keywords}
            name = ast.literal_eval(fields["key"])
            if name not in {"solarRadiation", "uv", "humidity", "winddir"}:
                continue
            value = eval(compile(ast.Expression(fields["value_fn"]), "<upstream-sensor-conversion>", "eval"), {"cast": cast})
            self.assertIsNone(value(None, ""))
            self.assertEqual(value(0, ""), 0)
            self.assertEqual(value(12, ""), 12)
            checked.add(name)
        self.assertEqual(checked, {"solarRadiation", "uv", "humidity", "winddir"})

    def test_sensor_unavailable_after_failed_poll_even_with_old_data(self):
        class Base:
            @property
            def available(self):
                return self.coordinator.last_update_success
        cls = methods(self.package / "sensor.py", "WundergroundPWSSensor", {"available"}, {}, Base)
        sensor = cls()
        sensor.coordinator = SimpleNamespace(data=CURRENT, last_update_success=False)
        self.assertFalse(sensor.available)
        sensor.coordinator.last_update_success = True
        self.assertTrue(sensor.available)
        sensor.coordinator.data = None
        self.assertFalse(sensor.available)

    def test_source_timestamp_is_utc_and_missing_or_invalid_is_unknown(self):
        tree = ast.parse((self.package / "wupws_obs_sensors.py").read_text())
        function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "utc_observation_time")
        namespace = {"datetime": datetime, "timezone": timezone}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "<upstream-utc-parser>", "exec"), namespace)
        parse = namespace["utc_observation_time"]
        expected = datetime(2026, 10, 17, 1, 2, 3, tzinfo=timezone.utc)
        self.assertEqual(parse("2026-10-17T01:02:03Z"), expected)
        self.assertEqual(parse("2026-10-16T21:02:03-04:00"), expected)
        self.assertEqual(parse("2026-10-17T01:02:03"), expected)
        for value in [None, "invalid", 123]:
            self.assertIsNone(parse(value))
        timestamp = next(call for call in ast.walk(tree) if isinstance(call, ast.Call) and any(item.arg == "key" and isinstance(item.value, ast.Constant) and item.value.value == "obsTimeUtc" for item in call.keywords))
        fields = {item.arg: item.value for item in timestamp.keywords}
        self.assertEqual(fields["device_class"].attr, "TIMESTAMP")

    def test_preparation_rejects_drift_and_existing_output(self):
        altered = Path(self.tmp.name) / "altered"
        shutil.copytree(Path(SOURCE) / PREPARE.COMPONENT, altered / PREPARE.COMPONENT)
        with (altered / PREPARE.COMPONENT / "coordinator.py").open("a") as stream:
            stream.write("\n# Unreviewed change\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            PREPARE.prepare(altered, Path(self.tmp.name) / "rejected")
        self.assertFalse((Path(self.tmp.name) / "rejected").exists())
        with self.assertRaises(FileExistsError):
            PREPARE.prepare(SOURCE, self.output)
        manifest = Path(self.prepared["manifest"]).read_bytes()
        self.assertEqual(PREPARE.sha256(manifest), self.prepared["manifest_sha256"])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    SOURCE = args.source
    unittest.main(argv=[__file__, *remaining])
