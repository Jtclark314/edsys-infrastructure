"""Offline regression checks for the private Home Assistant FindMy patch.

Runs exact integration methods extracted with AST, replacing only HA framework
boundaries. Uses the pinned real FindMy 0.10.2 dependency and synthetic data.
No Home Assistant installation, Apple account, or network connection is used.
"""

from __future__ import annotations

import ast
import base64
import copy
import io
import logging
import os
import socket
import unittest
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from findmy import (
    AsyncAppleAccount,
    FindMyAccessory,
    FixedRollingKeyPairAccessory,
    InvalidStateError,
    KeyPair,
    LoginState,
    UnauthorizedError,
    UnhandledProtocolError,
)

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = Path(os.environ["FINDMY_COMPONENT_DIR"])


class ConfigEntryAuthFailed(Exception):
    """HA boundary stub; exception semantics are checked below."""


def load_method(filename, classname, methodname, extra=None):
    """Compile the exact current method without importing Home Assistant."""
    source = ast.parse((COMPONENT / filename).read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == classname)
    method = copy.deepcopy(next(
        n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == methodname
    ))
    method.decorator_list = []
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), method],
        type_ignores=[],
    )
    logger = logging.getLogger(f"findmy-check.{filename}.{methodname}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    stream = io.StringIO()
    logger.handlers[:] = [logging.StreamHandler(stream)]
    namespace = {
        "_LOGGER": logger,
        "EntryDataAccount": dict,
        "EntryDataStaticDevice": dict,
        "ConfigEntryAuthFailed": ConfigEntryAuthFailed,
        "InvalidStateError": InvalidStateError,
        "UnauthorizedError": UnauthorizedError,
        "UnhandledProtocolError": UnhandledProtocolError,
        "KeyPair": KeyPair,
        "FindMyDevice": KeyPair | FindMyAccessory | FixedRollingKeyPairAccessory,
        "CoordinatorLogEvent": SimpleNamespace(
            FETCH_START="start", FETCH_SUCCESS="success", FETCH_ERROR="error",
        ),
    }
    namespace.update(extra or {})
    exec(compile(ast.fix_missing_locations(module), str(COMPONENT / filename), "exec"), namespace)
    return namespace[methodname], stream


def account_data(password="synthetic-only-password"):
    return {
        "type": "account",
        "ids": {"uid": "synthetic-user-id", "devid": "synthetic-device-id"},
        "account": {
            "username": "synthetic@example.invalid",
            "password": password,
            "info": {"account_name": "Synthetic Offline Test"},
        },
        "login": {
            "state": LoginState.LOGGED_IN.value,
            "data": {
                "dsid": "synthetic-dsid",
                "mobileme_data": {"tokens": {"searchPartyToken": "synthetic-token"}},
            },
        },
        "anisette": {"type": "aniRemote", "url": "https://example.invalid"},
    }


class FlowBoundary:
    _reauth_entry = None

    def async_create_entry(self, **kwargs):
        return {"result_type": "create", **kwargs}

    def async_update_reload_and_abort(self, entry, **kwargs):
        return {"result_type": "reauth", "entry": entry, **kwargs}


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.ok = status == 200

    def text(self):
        return "synthetic response"

    def json(self):
        return {"acsnLocations": {"statusCode": "200", "locationPayload": []}}


class PrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.network_block = patch.object(
            socket.socket, "connect", side_effect=AssertionError("Network access forbidden in checks"),
        )
        self.network_block.start()
        self.accounts = []

    async def asyncTearDown(self):
        for account in self.accounts:
            await account.close()
        self.network_block.stop()

    def restored(self, data):
        account = AsyncAppleAccount.from_json(data)
        self.accounts.append(account)
        return account

    async def test_new_and_reauth_entries_exclude_password_preserve_session(self):
        method, _ = load_method("config_flow.py", "InitialSetupConfigFlow", "async_step_acc_done")
        for reauth in (False, True):
            flow = FlowBoundary()
            flow._account = self.restored(account_data())
            flow._reauth_entry = "synthetic-entry" if reauth else None
            result = await method(flow)
            saved = result["data"]["account_data"]
            self.assertIsNone(saved["account"]["password"])
            self.assertNotIn("synthetic-only-password", repr(saved))
            self.assertEqual(saved["login"], account_data()["login"])
            self.assertEqual(saved["ids"], account_data()["ids"])
            self.assertEqual(result["result_type"], "reauth" if reauth else "create")
            # The persisted representation can be restored by the real dependency.
            self.assertEqual(self.restored(saved).login_state, LoginState.LOGGED_IN)

    async def test_restored_session_fetch_uses_token_without_password(self):
        account = self.restored(account_data(None))
        account.get_anisette_headers = AsyncMock(return_value={})
        account._http.post = AsyncMock(return_value=FakeResponse(200))
        tag = KeyPair(private_key=bytes.fromhex("01" * 28))
        result = await account.fetch_location([tag])
        self.assertEqual(result, {tag: None})
        calls = account._http.post.await_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["auth"], ("synthetic-dsid", "synthetic-token"))
        self.assertIsNone(account.to_json()["account"]["password"])

    async def test_expired_session_becomes_ha_reauth(self):
        account = self.restored(account_data(None))
        account.get_anisette_headers = AsyncMock(return_value={})
        account._http.post = AsyncMock(return_value=FakeResponse(401))
        method, _ = load_method("coordinator.py", "FindMyCoordinator", "_async_update_data")
        events = []
        coordinator = SimpleNamespace(
            get_account=lambda: account,
            devices=[KeyPair(private_key=bytes.fromhex("01" * 28))],
            data=None,
            _log=lambda acc, event: events.append(event),
        )
        with self.assertRaisesRegex(ConfigEntryAuthFailed, "Apple session expired"):
            await method(coordinator)
        self.assertEqual(events, ["start", "error"])
        self.assertEqual(account._http.post.await_count, 1)

    async def test_unrelated_value_error_is_not_hidden_as_auth_failure(self):
        method, _ = load_method("coordinator.py", "FindMyCoordinator", "_async_update_data")
        account = SimpleNamespace(fetch_location=AsyncMock(side_effect=ValueError("bad location payload")))
        events = []
        coordinator = SimpleNamespace(
            get_account=lambda: account, devices=[], data=None,
            _log=lambda acc, event: events.append(event),
        )
        with self.assertRaisesRegex(ValueError, "bad location payload"):
            await method(coordinator)
        self.assertEqual(events, ["start", "error"])

    async def test_2fa_debug_output_redacts_code(self):
        method, stream = load_method("config_flow.py", "InitialSetupConfigFlow", "async_step_acc_2fa_submit")
        flow = SimpleNamespace(
            _2fa_method=SimpleNamespace(submit=AsyncMock()),
            async_step_acc_done=AsyncMock(return_value={"done": True}),
        )
        result = await method(flow, {"code": "123456"})
        self.assertTrue(result["done"])
        self.assertNotIn("123456", stream.getvalue())
        self.assertIn("**REDACTED**", stream.getvalue())
        flow._2fa_method.submit.assert_awaited_once_with("123456")

    async def test_static_tag_debug_output_redacts_private_key(self):
        method, stream = load_method("config_flow.py", "InitialSetupConfigFlow", "async_step_dev_static")
        key = base64.b64encode(bytes.fromhex("01" * 28)).decode()
        result = await method(FlowBoundary(), {"name": "Synthetic tag", "private_key": key})
        self.assertEqual(result["data"]["type"], "device_static")
        self.assertNotIn(key, stream.getvalue())
        self.assertIn("**REDACTED**", stream.getvalue())

    async def test_exporter_json_schema_preserves_alignment_and_derives_keys(self):
        now = datetime.now(tz=UTC).isoformat()
        exported = {
            "type": "accessory", "master_key": "01" * 28,
            "skn": "02" * 32, "sks": "03" * 32,
            "paired_at": now, "alignment_date": now, "alignment_index": 96,
            "name": "Synthetic tag", "emoji": "", "model": "Synthetic",
            "identifier": "synthetic-accessory", "group_identifier": None,
            "serial_number": None,
        }
        accessory = FindMyAccessory.from_json(exported)
        self.assertEqual(accessory.to_json()["alignment_index"], 96)
        self.assertEqual(accessory.to_json()["alignment_date"], now)
        self.assertTrue(accessory.keys_at(96))


if __name__ == "__main__":
    assert version("FindMy") == "0.10.2", "Checks require the pinned dependency"
    unittest.main(verbosity=2)
