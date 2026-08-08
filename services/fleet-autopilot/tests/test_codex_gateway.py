from __future__ import annotations

import asyncio
import json
import stat
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from edsys_fleet.codex_gateway import CodexGateway, ensure_runtime_token


class FakeAppServer:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        value = json.loads(raw)
        self.sent.append(value)
        if value.get("method") == "initialize":
            await self.incoming.put(
                json.dumps(
                    {
                        "id": value["id"],
                        "result": {"platformFamily": "unix", "platformOs": "linux"},
                    }
                )
            )
        elif value.get("id") is not None:
            await self.incoming.put(
                json.dumps({"id": value["id"], "result": {"echo": value.get("method")}})
            )

    async def recv(self) -> str:
        return await self.incoming.get()

    def __aiter__(self) -> FakeAppServer:
        return self

    async def __anext__(self) -> str:
        return await self.incoming.get()


@pytest.mark.asyncio
async def test_gateway_authenticates_initializes_and_proxies(tmp_path: Path) -> None:
    fake = FakeAppServer()

    @asynccontextmanager
    async def connector(_: Path):
        yield fake

    gateway = CodexGateway(
        socket_path=tmp_path / "gateway/gateway.sock",
        token_path=tmp_path / "gateway/gateway.token",
        app_server_socket=tmp_path / "app-server.sock",
        connector=connector,
    )
    await gateway.start()
    reader, writer = await asyncio.open_unix_connection(str(gateway.socket_path))
    writer.write(
        json.dumps(
            {
                "type": "connect",
                "token": gateway.token,
                "clientInfo": {"name": "test", "title": "Test", "version": "1"},
            }
        ).encode()
        + b"\n"
    )
    await writer.drain()
    ready = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    assert ready["type"] == "ready"
    assert ready["initialize"]["platformOs"] == "linux"
    assert fake.sent[0]["params"]["capabilities"]["experimentalApi"] is True
    assert fake.sent[1] == {"method": "initialized", "params": {}}

    writer.write(b'{"id":7,"method":"model/list","params":{}}\n')
    await writer.drain()
    response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    assert response == {"id": 7, "result": {"echo": "model/list"}}
    writer.close()
    await writer.wait_closed()
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_token_without_connecting(tmp_path: Path) -> None:
    called = False

    @asynccontextmanager
    async def connector(_: Path):
        nonlocal called
        called = True
        yield FakeAppServer()

    gateway = CodexGateway(
        socket_path=tmp_path / "gateway/gateway.sock",
        token_path=tmp_path / "gateway/gateway.token",
        app_server_socket=tmp_path / "app-server.sock",
        connector=connector,
    )
    await gateway.start()
    reader, writer = await asyncio.open_unix_connection(str(gateway.socket_path))
    writer.write(b'{"type":"connect","token":"wrong","clientInfo":{"name":"test"}}\n')
    await writer.drain()
    response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    assert response["type"] == "error"
    assert "Unauthorized" in response["message"]
    assert called is False
    writer.close()
    await writer.wait_closed()
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_close_disconnects_active_client_promptly(tmp_path: Path) -> None:
    fake = FakeAppServer()

    @asynccontextmanager
    async def connector(_: Path):
        yield fake

    gateway = CodexGateway(
        socket_path=tmp_path / "gateway/gateway.sock",
        token_path=tmp_path / "gateway/gateway.token",
        app_server_socket=tmp_path / "app-server.sock",
        connector=connector,
    )
    await gateway.start()
    reader, writer = await asyncio.open_unix_connection(str(gateway.socket_path))
    writer.write(
        json.dumps(
            {
                "type": "connect",
                "token": gateway.token,
                "clientInfo": {"name": "test", "title": "Test", "version": "1"},
            }
        ).encode()
        + b"\n"
    )
    await writer.drain()
    assert json.loads(await asyncio.wait_for(reader.readline(), timeout=2))["type"] == "ready"

    await asyncio.wait_for(gateway.close(), timeout=3)
    assert await asyncio.wait_for(reader.readline(), timeout=2) == b""
    writer.close()
    await writer.wait_closed()


def test_runtime_token_is_long_private_and_stable(tmp_path: Path) -> None:
    path = tmp_path / "runtime/gateway.token"
    first = ensure_runtime_token(path)
    second = ensure_runtime_token(path)
    assert first == second
    assert len(first) >= 48
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
