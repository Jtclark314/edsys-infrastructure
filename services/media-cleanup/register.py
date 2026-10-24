#!/usr/bin/env python3
"""Register only this tool's stdio block; preserve all other Codex settings."""
import copy
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import tomllib


def main():
    config = Path.home() / ".codex/config.toml"
    content = config.read_text()
    old = tomllib.loads(content)
    name = "edsys-media-cleanup"
    launcher = str(Path.home() / ".local/bin/edsys-media-cleanup-mcp")
    expected = {"command": launcher, "enabled": True, "startup_timeout_sec": 20, "tool_timeout_sec": 900,
                "enabled_tools": ["media_delete", "media_status"],
                "env": {"PYTHONUNBUFFERED": "1"}}
    existing = old.get("mcp_servers", {}).get(name)
    if existing == expected:
        print(json.dumps({"registered": True, "changed": False}))
        return
    if existing is not None:
        legacy = {**expected, "enabled_tools": ["media_find", "media_plan", "media_execute", "media_status"]}
        if existing != legacy:
            raise SystemExit("An existing Media Cleanup registration differs; inspect it before changing it")
    assert Path(launcher).is_file(), "Install the launcher before registering it"
    block = '\n[mcp_servers.edsys-media-cleanup]\ncommand = ' + json.dumps(launcher) + '\nenabled = true\nstartup_timeout_sec = 20\ntool_timeout_sec = 900\nenabled_tools = ["media_delete", "media_status"]\n\n[mcp_servers.edsys-media-cleanup.env]\nPYTHONUNBUFFERED = "1"\n'
    if existing is None:
        updated = content.rstrip() + "\n" + block
    else:
        section = re.search(r"(?ms)^\[mcp_servers\.edsys-media-cleanup\]\s*\n(.*?)(?=^\[|\Z)", content)
        assert section, "Media Cleanup section not found"
        body, count = re.subn(r"(?m)^enabled_tools\s*=.*$", 'enabled_tools = ["media_delete", "media_status"]', section[1])
        assert count == 1, "Expected one tool allowlist"
        updated = content[:section.start(1)] + body + content[section.end(1):]
    parsed = tomllib.loads(updated)
    assert parsed["mcp_servers"][name] == expected
    del parsed["mcp_servers"][name]
    original_without_server = copy.deepcopy(old)
    original_without_server.get("mcp_servers", {}).pop(name, None)
    assert parsed == original_without_server, "Registration would change unrelated settings"
    private = Path.home() / ".local/state/edsys-media-cleanup/registration-backups"
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(private, 0o700)
    backup = private / ("config-" + secrets.token_hex(8) + ".toml")
    shutil.copyfile(config, backup)
    os.chmod(backup, 0o600)
    temp = config.with_name(".media-cleanup-config-" + secrets.token_hex(8))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(updated)
        stream.flush()
        os.fsync(stream.fileno())
    assert config.read_text() == content, "Codex config changed concurrently; inspect before retrying"
    os.replace(temp, config)
    print(json.dumps({"registered": True, "changed": True, "other_settings_preserved": True, "tool_count": 2}))


if __name__ == "__main__":
    main()
