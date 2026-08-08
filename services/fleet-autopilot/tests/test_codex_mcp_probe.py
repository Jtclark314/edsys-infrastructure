from __future__ import annotations

from edsys_fleet.probes import codex_mcp_probe


def test_probe_selects_ultra_through_thread_settings_without_model_call(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeAppServer:
        def call(self, method: str, params: dict, timeout: int = 90) -> dict:
            del timeout
            calls.append((method, params))
            if method == "initialize":
                return {"userAgent": "fleet-test"}
            if method == "thread/start":
                return {
                    "thread": {"id": "thread-test", "ephemeral": True},
                    "model": "gpt-5.6-sol",
                    "serviceTier": "priority",
                    "approvalPolicy": "never",
                    "sandbox": {"type": "dangerFullAccess"},
                    "activePermissionProfile": {"id": ":danger-full-access"},
                    "reasoningEffort": None,
                }
            if method == "thread/settings/update":
                return {}
            if method == "mcpServerStatus/list":
                required = {
                    "cloudflare-api": "execute",
                    "codex_apps": "github.get_profile",
                    "edsys_code_intelligence": "code_index_status",
                    "openaiDeveloperDocs": "search_openai_docs",
                    "playwright-local": "browser_navigate",
                    "proxmox": "proxmox_cluster_status",
                }
                return {
                    "data": [
                        {"name": name, "tools": {tool: {}}}
                        for name, tool in required.items()
                    ]
                }
            raise AssertionError(f"unexpected app-server method: {method}")

        def close(self) -> None:
            return None

    def fake_tool_call(server, thread_id, mcp_server, tool, arguments):
        del server, thread_id, tool, arguments
        text = '{"success":true,"hasResult":true}' if mcp_server == "cloudflare-api" else "ok"
        return {"content": [{"type": "text", "text": text}]}

    monkeypatch.setattr(codex_mcp_probe, "AppServer", FakeAppServer)
    monkeypatch.setattr(codex_mcp_probe, "tool_call", fake_tool_call)

    result = codex_mcp_probe.run()

    thread_start = next(params for method, params in calls if method == "thread/start")
    settings_update = next(
        params for method, params in calls if method == "thread/settings/update"
    )
    assert "reasoningEffort" not in thread_start
    assert settings_update == {"threadId": "thread-test", "effort": "ultra"}
    assert result["model_call"] is False
    assert result["thread"]["reasoning_effort"] == "ultra"
    assert result["thread"]["reasoning_selection"] == "thread/settings/update accepted"

