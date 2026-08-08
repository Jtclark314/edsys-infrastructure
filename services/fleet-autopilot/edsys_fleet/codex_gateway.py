from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import secrets
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import websockets

LOGGER = logging.getLogger("edsys_fleet.codex_gateway")
MAX_FRAME_BYTES = 8 * 1024 * 1024


class CodexGatewayError(RuntimeError):
    pass


def _runtime_root() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    return base / "edsys-codex-gateway"


def _write_private(path: Path, value: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
    os.chmod(path.parent, 0o770)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def ensure_runtime_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if len(token) < 48:
        token = secrets.token_urlsafe(48)
        _write_private(path, token + "\n", 0o640)
    else:
        os.chmod(path, 0o640)
    return token


@asynccontextmanager
async def connect_app_server(path: Path) -> AsyncIterator[Any]:
    async with websockets.unix_connect(
        path=str(path),
        uri="ws://localhost/",
        proxy=None,
        compression=None,
        open_timeout=10,
        close_timeout=5,
        max_size=MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
    ) as connection:
        yield connection


class CodexGateway:
    def __init__(
        self,
        *,
        socket_path: Path,
        token_path: Path,
        app_server_socket: Path,
        connector: Callable[[Path], Any] = connect_app_server,
    ) -> None:
        self.socket_path = socket_path
        self.token_path = token_path
        self.app_server_socket = app_server_socket
        self.connector = connector
        self.token = ""
        self.server: asyncio.AbstractServer | None = None
        self.started = asyncio.Event()
        self.client_writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self.token = ensure_runtime_token(self.token_path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o770)
        os.chmod(self.socket_path.parent, 0o770)
        self.socket_path.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
            limit=MAX_FRAME_BYTES,
        )
        os.chmod(self.socket_path, 0o660)
        self.started.set()
        LOGGER.info(
            "Codex Portal gateway ready socket=%s app_server_socket=%s",
            self.socket_path,
            self.app_server_socket,
        )

    async def close(self) -> None:
        if self.server:
            self.server.close()
        writers = list(self.client_writers)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(self._wait_closed(writer) for writer in writers),
                return_exceptions=True,
            )
        if self.server:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.server.wait_closed(), timeout=3)
        self.socket_path.unlink(missing_ok=True)

    async def serve(self) -> None:
        await self.start()
        assert self.server
        async with self.server:
            await self.server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.client_writers.add(writer)
        try:
            hello = await asyncio.wait_for(reader.readline(), timeout=8)
            if not hello or len(hello) > 64 * 1024:
                raise CodexGatewayError("Missing gateway handshake")
            message = json.loads(hello)
            if message.get("type") != "connect" or not hmac.compare_digest(
                str(message.get("token") or ""), self.token
            ):
                raise CodexGatewayError("Unauthorized gateway client")
            client_info = dict(message.get("clientInfo") or {})
            if not client_info.get("name"):
                raise CodexGatewayError("Gateway clientInfo.name is required")
            capabilities = dict(message.get("capabilities") or {})
            capabilities["experimentalApi"] = True
            async with self.connector(self.app_server_socket) as app_server:
                initialized = await self._initialize_app_server(
                    app_server, client_info, capabilities
                )
                await self._write_line(
                    writer,
                    {
                        "type": "ready",
                        "appServerSocket": str(self.app_server_socket),
                        "initialize": initialized,
                    },
                )
                await self._proxy(reader, writer, app_server)
        except (TimeoutError, json.JSONDecodeError, CodexGatewayError) as exc:
            LOGGER.warning(
                "Codex gateway client rejected reason=%s", type(exc).__name__
            )
            with suppress(Exception):
                await self._write_line(writer, {"type": "error", "message": str(exc)})
        except (OSError, websockets.WebSocketException) as exc:
            LOGGER.warning(
                "Codex gateway connection failed reason=%s", type(exc).__name__
            )
            with suppress(Exception):
                await self._write_line(
                    writer,
                    {
                        "type": "error",
                        "message": "Managed Codex app-server is unavailable.",
                    },
                )
        except Exception:
            LOGGER.exception("Unexpected Codex gateway client failure")
        finally:
            writer.close()
            await self._wait_closed(writer)
            self.client_writers.discard(writer)

    @staticmethod
    async def _wait_closed(writer: asyncio.StreamWriter) -> None:
        with suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(writer.wait_closed(), timeout=2)

    async def _initialize_app_server(
        self,
        app_server: Any,
        client_info: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = "edsys-gateway-initialize"
        await app_server.send(
            json.dumps(
                {
                    "method": "initialize",
                    "id": request_id,
                    "params": {
                        "clientInfo": client_info,
                        "capabilities": capabilities,
                    },
                },
                separators=(",", ":"),
            )
        )
        while True:
            raw = await asyncio.wait_for(app_server.recv(), timeout=20)
            value = json.loads(raw)
            if value.get("id") != request_id:
                continue
            if value.get("error"):
                raise CodexGatewayError(
                    f"Codex app-server initialization failed: {value['error'].get('message', 'unknown error')}"
                )
            await app_server.send(
                json.dumps(
                    {"method": "initialized", "params": {}}, separators=(",", ":")
                )
            )
            return dict(value.get("result") or {})

    async def _proxy(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        app_server: Any,
    ) -> None:
        async def client_to_app_server() -> None:
            while True:
                line = await reader.readline()
                if not line:
                    return
                if len(line) > MAX_FRAME_BYTES:
                    raise CodexGatewayError("Gateway client frame is too large")
                value = json.loads(line)
                if value.get("method") in {"initialize", "initialized"}:
                    raise CodexGatewayError(
                        "Gateway owns the app-server initialization lifecycle"
                    )
                await app_server.send(json.dumps(value, separators=(",", ":")))

        async def app_server_to_client() -> None:
            async for raw in app_server:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                value = json.loads(raw)
                await self._write_line(writer, value)

        tasks = {
            asyncio.create_task(client_to_app_server()),
            asyncio.create_task(app_server_to_client()),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()

    @staticmethod
    async def _write_line(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > MAX_FRAME_BYTES:
            raise CodexGatewayError("App-server frame is too large")
        writer.write(payload)
        await writer.drain()


async def run_gateway(args: argparse.Namespace) -> None:
    gateway = CodexGateway(
        socket_path=args.socket,
        token_path=args.token_file,
        app_server_socket=args.app_server_socket,
    )
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    task = asyncio.create_task(gateway.serve())
    await gateway.started.wait()
    await stop.wait()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await gateway.close()


def main() -> None:
    runtime = _runtime_root()
    parser = argparse.ArgumentParser(
        description="Private Unix-socket bridge to the managed Codex app-server"
    )
    parser.add_argument("--socket", type=Path, default=runtime / "gateway.sock")
    parser.add_argument("--token-file", type=Path, default=runtime / "gateway.token")
    parser.add_argument(
        "--app-server-socket",
        type=Path,
        default=Path.home() / ".codex/app-server-control/app-server-control.sock",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_gateway(args))


if __name__ == "__main__":
    main()
