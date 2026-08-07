#!/usr/bin/env python3
from __future__ import annotations

import json
import select
import subprocess
import time
from pathlib import Path
from typing import Any


class ProbeError(RuntimeError):
    pass


class AppServer:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.next_id = 1

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)

    def call(self, method: str, params: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(
            json.dumps(
                {"id": request_id, "method": method, "params": params},
                separators=(",", ":"),
            )
            + "\n"
        )
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], min(1, deadline - time.monotonic()))
            if not ready:
                if self.process.poll() is not None:
                    break
                continue
            line = self.process.stdout.readline()
            if not line:
                break
            value = json.loads(line)
            if value.get("id") == request_id:
                if value.get("error"):
                    raise ProbeError(f"{method} returned an app-server error")
                return dict(value.get("result") or {})
        stderr = ""
        if self.process.poll() is not None and self.process.stderr:
            stderr = self.process.stderr.read()[-500:]
        raise ProbeError(f"{method} timed out or app-server exited: {stderr}")


def tool_call(
    server: AppServer,
    thread_id: str,
    mcp_server: str,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = server.call(
        "mcpServer/tool/call",
        {
            "server": mcp_server,
            "tool": tool,
            "arguments": arguments,
            "threadId": thread_id,
        },
        timeout=120,
    )
    if result.get("isError"):
        raise ProbeError(f"{mcp_server}.{tool} returned isError")
    return result


def content_text(result: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get("text") or "")
        for item in (result.get("content") or [])
        if isinstance(item, dict)
    )


def run() -> dict[str, Any]:
    app = AppServer()
    calls: dict[str, dict[str, Any]] = {}
    try:
        initialized = app.call(
            "initialize",
            {
                "clientInfo": {"name": "edsys-fleet-deterministic", "version": "2.0.0"},
                "capabilities": {"experimentalApi": True},
            },
            timeout=30,
        )
        started = app.call(
            "thread/start",
            {
                "cwd": str(Path.home() / "code" / "EdSys-Master"),
                "ephemeral": True,
                "permissions": ":danger-full-access",
                "approvalPolicy": "never",
                "model": "gpt-5.6-sol",
                "serviceTier": "priority",
                "reasoningEffort": "ultra",
            },
            timeout=60,
        )
        thread = dict(started.get("thread") or {})
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise ProbeError("app-server did not create an ephemeral probe thread")
        if started.get("approvalPolicy") != "never":
            raise ProbeError("probe thread did not retain approval Never")
        if dict(started.get("sandbox") or {}).get("type") != "dangerFullAccess":
            raise ProbeError("probe thread did not retain danger-full-access")
        if str((started.get("activePermissionProfile") or {}).get("id")) != ":danger-full-access":
            raise ProbeError("probe thread active permission profile was reduced")
        if started.get("model") != "gpt-5.6-sol":
            raise ProbeError("probe thread did not retain gpt-5.6-sol")
        if started.get("serviceTier") != "priority":
            raise ProbeError("probe thread did not retain Priority processing")
        if str(started.get("reasoningEffort") or "").lower() != "ultra":
            raise ProbeError("probe thread did not retain Ultra reasoning")
        inventory = app.call(
            "mcpServerStatus/list",
            {"threadId": thread_id, "detail": "full", "limit": 100},
            timeout=120,
        )
        servers = {
            str(item.get("name")): item
            for item in inventory.get("data") or []
            if isinstance(item, dict)
        }
        required = {
            "cloudflare-api": "execute",
            "codex_apps": "github.get_profile",
            "edsys_code_intelligence": "code_index_status",
            "openaiDeveloperDocs": "search_openai_docs",
            "playwright-local": "browser_navigate",
            "proxmox": "proxmox_cluster_status",
        }
        for name, tool in required.items():
            if name not in servers or tool not in dict(servers[name].get("tools") or {}):
                raise ProbeError(f"required MCP path is absent: {name}.{tool}")

        result = tool_call(app, thread_id, "proxmox", "proxmox_cluster_status", {})
        calls["proxmox"] = {"passed": True, "content_blocks": len(result.get("content") or [])}

        result = tool_call(app, thread_id, "edsys_code_intelligence", "code_index_status", {})
        calls["code_intelligence"] = {"passed": True, "content_blocks": len(result.get("content") or [])}

        result = tool_call(
            app,
            thread_id,
            "openaiDeveloperDocs",
            "search_openai_docs",
            {"query": "Codex MCP configuration", "limit": 1},
        )
        calls["openai_docs"] = {"passed": True, "content_blocks": len(result.get("content") or [])}

        result = tool_call(app, thread_id, "codex_apps", "github.get_profile", {})
        calls["github"] = {
            "passed": True,
            "authenticated": True,
            "content_blocks": len(result.get("content") or []),
        }

        cloudflare_code = (
            'async () => { const r = await cloudflare.request({method:"GET",path:"/user"}); '
            "return {success:r.success,httpStatus:r.status,hasResult:Boolean(r.result)}; }"
        )
        result = tool_call(
            app,
            thread_id,
            "cloudflare-api",
            "execute",
            {"code": cloudflare_code},
        )
        cloudflare_text = content_text(result).replace(" ", "").lower()
        if '"success":true' not in cloudflare_text or '"hasresult":true' not in cloudflare_text:
            raise ProbeError("Cloudflare representative call did not prove authenticated data")
        calls["cloudflare"] = {
            "passed": True,
            "authenticated": True,
            "content_blocks": len(result.get("content") or []),
        }

        result = tool_call(
            app,
            thread_id,
            "playwright-local",
            "browser_navigate",
            {"url": "https://example.com"},
        )
        calls["browser"] = {"passed": True, "content_blocks": len(result.get("content") or [])}
        try:
            tool_call(app, thread_id, "playwright-local", "browser_close", {})
        except ProbeError:
            calls["browser"]["cleanup"] = "failed"
            raise
        calls["browser"]["cleanup"] = "passed"

        return {
            "status": "passed",
            "model_call": False,
            "client_user_agent": str(initialized.get("userAgent") or "")[:160],
            "thread": {
                "ephemeral": bool(thread.get("ephemeral")),
                "model": started.get("model"),
                "service_tier": started.get("serviceTier"),
                "reasoning_effort": started.get("reasoningEffort"),
                "approval_policy": started.get("approvalPolicy"),
                "permission_profile": (started.get("activePermissionProfile") or {}).get("id"),
            },
            "initialized_servers": sorted(required),
            "calls": calls,
        }
    finally:
        app.close()


if __name__ == "__main__":
    try:
        print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(1)
