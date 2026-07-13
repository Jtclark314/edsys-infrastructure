#!/usr/bin/env python3
"""Render and effectively validate the complete EdSys Share Samba config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SECTION_PATTERN = re.compile(
    r"(?ms)(^[ \t]*\[(?P<name>[^\n\]]+)\][ \t]*\n)(?P<body>.*?)(?=^[ \t]*\[[^\n\]]+\][ \t]*\n|\Z)"
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<name>[^#; \t=\r\n][^=\r\n]*?)"
    r"[ \t]*=[ \t]*(?P<value>[^\r\n]*)(?P<newline>\r?\n|\Z)"
)

TARGET_SECTION = "EdSys-Share"
EXPECTED_PATH = "/EdSys-Share"
EXPECTED_FORCE_USER = "jeremy"
EXPECTED_PREEXEC = "/usr/local/libexec/edsys-share/edsys-share-mount-check-smb"

GLOBAL_MANAGED_OPTIONS = {
    "interfaces",
    "bind interfaces only",
    "hosts allow",
    "allow hosts",
    "hosts deny",
    "deny hosts",
    "config backend",
    "registry shares",
    "usershare max shares",
    "default service",
    "default",
    "auto services",
    "preload",
    "load printers",
    "server role",
    "security",
    "passdb backend",
    "username map",
    "username map script",
    "root directory",
    "root dir",
    "root",
    "smb ports",
}
DYNAMIC_CONFIG_OPTIONS = {"include", "config file"}
TARGET_FRAGMENT_OPTIONS = {
    "path",
    "available",
    "browseable",
    "printable",
    "max connections",
    "server addresses",
    "msdfs root",
    "msdfs proxy",
    "magic script",
    "magic output",
    "read only",
    "guest ok",
    "guest only",
    "valid users",
    "invalid users",
    "admin users",
    "force user",
    "force group",
    "read list",
    "write list",
    "veto files",
    "vfs objects",
    "preexec",
    "postexec",
    "root postexec",
    "create mask",
    "force create mode",
    "directory mask",
    "force directory mode",
    "store dos attributes",
    "follow symlinks",
    "wide links",
    "smb encrypt",
    "root preexec",
    "root preexec close",
}


def normalize_option_name(name: str) -> str:
    """Match Samba's case-insensitive, whitespace-insensitive parameter names."""
    return "".join(name.split()).casefold()


def normalize_section_key(name: str) -> str:
    """Match Samba's case- and whitespace-insensitive service names."""
    compact = "".join(name.split()).casefold()
    if compact == "globals":
        return "global"
    return compact


def assignment_matches(text: str, option: str) -> list[re.Match[str]]:
    wanted = normalize_option_name(option)
    return [
        match
        for match in ASSIGNMENT_PATTERN.finditer(text)
        if normalize_option_name(match.group("name")) == wanted
    ]


def assignment_matches_any(
    text: str, options: set[str] | tuple[str, ...]
) -> list[re.Match[str]]:
    wanted = {normalize_option_name(option) for option in options}
    return [
        match
        for match in ASSIGNMENT_PATTERN.finditer(text)
        if normalize_option_name(match.group("name")) in wanted
    ]


def section_matches(text: str, section: str) -> list[re.Match[str]]:
    wanted = normalize_section_key(section)
    return [
        match
        for match in SECTION_PATTERN.finditer(text)
        if normalize_section_key(match.group("name")) == wanted
    ]


def validate_physical_syntax(
    text: str, label: str, *, reject_continuations: bool = True
) -> None:
    """Reject syntax the conservative renderer does not model exactly."""
    header_lines = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(("#", ";")):
            continue
        if reject_continuations and line.rstrip().endswith("\\"):
            raise SystemExit(
                f"{label}:{line_number}: Samba line continuations are unsupported"
            )
        if stripped.startswith("["):
            if re.fullmatch(r"\[[^\]\r\n]+\][ \t]*", stripped) is None:
                raise SystemExit(
                    f"{label}:{line_number}: unsupported Samba section-header syntax"
                )
            header_lines.append(line_number)

    parsed = list(SECTION_PATTERN.finditer(text))
    if len(parsed) != len(header_lines):
        raise SystemExit(
            f"{label}: every Samba section header must end with a newline and be parseable"
        )
    if parsed:
        prefix = text[: parsed[0].start()]
        for line_number, line in enumerate(prefix.splitlines(), start=1):
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", ";")):
                raise SystemExit(
                    f"{label}:{line_number}: active content before the first "
                    "Samba section is unsupported"
                )
    for match in parsed:
        section_name = match.group("name").strip()
        compact_name = "".join(section_name.split()).casefold()
        if (
            compact_name in {"global", "globals"}
            and section_name.casefold() != "global"
        ):
            raise SystemExit(
                f"{label}: Samba special global alias [{section_name}] is unsupported; "
                "use one [global]"
            )


def assert_unique_sections(text: str, label: str = "configuration") -> None:
    seen: set[str] = set()
    for match in SECTION_PATTERN.finditer(text):
        name = normalize_section_key(match.group("name"))
        if name in seen:
            raise SystemExit(
                f"{label}: duplicate Samba section [{match.group('name').strip()}]"
            )
        seen.add(name)


def reject_dynamic_sources(text: str, label: str) -> None:
    matches = assignment_matches_any(text, DYNAMIC_CONFIG_OPTIONS)
    if matches:
        option = matches[0].group("name").strip()
        raise SystemExit(
            f"{label}: Samba option '{option}' is unsupported because every "
            "service must be inspected before the restricted identity is enabled"
        )


def has_plain_terminal_list_member(value: str, member: str) -> bool:
    """Return true only for an unquoted, unescaped final Samba list token."""
    return (
        re.search(
            rf"(?:^|[ \t,]){re.escape(member)}$",
            value.strip(" \t"),
            flags=re.IGNORECASE,
        )
        is not None
    )


def require_single_option(body: str, option: str, section: str) -> str:
    matches = assignment_matches(body, option)
    if len(matches) != 1:
        raise SystemExit(
            f"[{section}] must contain exactly one effective '{option}' option"
        )
    return matches[0].group("value").strip(" \t")


def require_single_option_alias(
    body: str, options: tuple[str, ...], section: str
) -> str:
    matches = assignment_matches_any(body, options)
    if len(matches) != 1:
        joined = " or ".join(repr(option) for option in options)
        raise SystemExit(
            f"[{section}] must contain exactly one effective {joined} option"
        )
    return matches[0].group("value").strip(" \t")


def validate_fragment(fragment: str, restricted_user: str) -> None:
    validate_physical_syntax(fragment, "share fragment")
    reject_dynamic_sources(fragment, "share fragment")
    assert_unique_sections(fragment, "share fragment")

    sections = list(SECTION_PATTERN.finditer(fragment))
    if (
        len(sections) != 1
        or sections[0].group("name").strip().casefold() != TARGET_SECTION.casefold()
    ):
        raise SystemExit(
            f"Share fragment must define exactly one [{TARGET_SECTION}] section"
        )

    if assignment_matches_any(fragment, GLOBAL_MANAGED_OPTIONS):
        raise SystemExit("Share fragment may not override managed global Samba policy")

    body = sections[0].group("body")
    assignments = list(ASSIGNMENT_PATTERN.finditer(body))
    assignment_lines = {body.count("\n", 0, match.start()) + 1 for match in assignments}
    for line_number, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if (
            stripped
            and not stripped.startswith(("#", ";"))
            and line_number not in assignment_lines
        ):
            raise SystemExit(
                f"[{TARGET_SECTION}] contains unsupported active syntax on "
                f"body line {line_number}"
            )

    allowed = {normalize_option_name(option) for option in TARGET_FRAGMENT_OPTIONS}
    counts: dict[str, int] = {}
    for assignment in assignments:
        name = normalize_option_name(assignment.group("name"))
        if name not in allowed:
            raise SystemExit(
                f"[{TARGET_SECTION}] option "
                f"'{assignment.group('name').strip()}' is not allowed"
            )
        counts[name] = counts.get(name, 0) + 1
    if set(counts) != allowed or any(count != 1 for count in counts.values()):
        raise SystemExit(
            f"[{TARGET_SECTION}] must contain each managed option exactly once"
        )

    if require_single_option(body, "path", TARGET_SECTION) != EXPECTED_PATH:
        raise SystemExit(f"[{TARGET_SECTION}] path must be {EXPECTED_PATH}")
    if require_single_option(body, "force user", TARGET_SECTION) != EXPECTED_FORCE_USER:
        raise SystemExit(f"[{TARGET_SECTION}] force user must be {EXPECTED_FORCE_USER}")

    valid_users = require_single_option(body, "valid users", TARGET_SECTION)
    if valid_users != f"{EXPECTED_FORCE_USER} {restricted_user}":
        raise SystemExit(
            f"[{TARGET_SECTION}] valid users must contain only "
            f"{EXPECTED_FORCE_USER} and {restricted_user}"
        )
    invalid_matches = assignment_matches(body, "invalid users")
    if invalid_matches[0].group("value").strip(" \t"):
        raise SystemExit(f"[{TARGET_SECTION}] invalid users must remain empty")
    admin_matches = assignment_matches(body, "admin users")
    if admin_matches[0].group("value").strip(" \t"):
        raise SystemExit(f"[{TARGET_SECTION}] admin users must remain empty")

    required_values = {
        "guest ok": "no",
        "available": "yes",
        "browseable": "yes",
        "printable": "no",
        "max connections": "0",
        "server addresses": "",
        "msdfs root": "no",
        "msdfs proxy": "",
        "magic script": "",
        "magic output": "",
        "read only": "no",
        "guest only": "no",
        "force group": "",
        "read list": "",
        "write list": "",
        "veto files": "",
        "vfs objects": "",
        "preexec": "",
        "postexec": "",
        "root postexec": "",
        "create mask": "0660",
        "force create mode": "0660",
        "directory mask": "0770",
        "force directory mode": "0770",
        "store dos attributes": "yes",
        "follow symlinks": "no",
        "wide links": "no",
        "root preexec close": "yes",
    }
    for option, expected in required_values.items():
        actual = require_single_option(body, option, TARGET_SECTION)
        if actual.casefold() != expected:
            raise SystemExit(
                f"[{TARGET_SECTION}] {option} must be {expected}, found {actual}"
            )
    encryption = require_single_option_alias(
        body, ("smb encrypt", "server smb encrypt"), TARGET_SECTION
    )
    if encryption.casefold() != "required":
        raise SystemExit(f"[{TARGET_SECTION}] SMB encryption must be required")
    if require_single_option(body, "root preexec", TARGET_SECTION) != EXPECTED_PREEXEC:
        raise SystemExit(
            f"[{TARGET_SECTION}] root preexec is not the fixed mount guard"
        )


def ensure_section_list_member(
    text: str, section: str, option: str, member: str
) -> str:
    matches = section_matches(text, section)
    if len(matches) != 1:
        raise SystemExit(f"Required Samba section [{section}] was not found")
    section_match = matches[0]
    body = section_match.group("body")

    option_matches = assignment_matches(body, option)
    if len(option_matches) > 1:
        raise SystemExit(
            f"Duplicate Samba option '{option}' in [{section}] would be ambiguous"
        )
    if not option_matches:
        body = f"   {option} = {member}\n" + body
    else:
        option_match = option_matches[0]
        value = option_match.group("value").rstrip()
        if not has_plain_terminal_list_member(value, member):
            value = f"{value} {member}" if value else member
        replacement = f"   {option} = {value}{option_match.group('newline')}"
        body = body[: option_match.start()] + replacement + body[option_match.end() :]

    return (
        text[: section_match.start()]
        + section_match.group(1)
        + body
        + text[section_match.end() :]
    )


def enforce_global_policy(text: str) -> str:
    matches = section_matches(text, "global")
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one [global] section, found {len(matches)}")
    match = matches[0]

    outside = text[: match.start()] + text[match.end() :]
    outside_managed = assignment_matches_any(outside, GLOBAL_MANAGED_OPTIONS)
    if outside_managed:
        option = outside_managed[0].group("name").strip()
        raise SystemExit(
            f"Managed global Samba option '{option}' exists outside [global]"
        )

    managed_normalized = {
        normalize_option_name(option) for option in GLOBAL_MANAGED_OPTIONS
    }

    def remove_managed(option_match: re.Match[str]) -> str:
        if normalize_option_name(option_match.group("name")) in managed_normalized:
            return ""
        return option_match.group(0)

    body = ASSIGNMENT_PATTERN.sub(remove_managed, match.group("body")).lstrip("\r\n")
    managed = (
        "   interfaces = lo enp7s0\n"
        "   bind interfaces only = yes\n"
        "   hosts allow = 127.0.0.1 192.168.50.0/24\n"
        "   hosts deny = 0.0.0.0/0\n"
        "   config backend = file\n"
        "   registry shares = no\n"
        "   usershare max shares = 0\n"
        "   default service =\n"
        "   auto services =\n"
        "   load printers = no\n"
        "   server role = standalone server\n"
        "   security = user\n"
        "   passdb backend = tdbsam\n"
        "   username map =\n"
        "   username map script =\n"
        "   root directory =\n"
        "   smb ports = 445\n"
    )
    replacement = match.group(1) + managed
    if body:
        replacement += "\n" + body
    return text[: match.start()] + replacement + text[match.end() :]


def remove_managed_share_sections(text: str) -> str:
    pattern = re.compile(
        r"(?ims)^(?:[ \t]*# EdSys Share:[^\n]*\n(?:[ \t]*\n)*)*"
        r"[ \t]*\[EdSys-Share\][ \t]*\n.*?"
        r"(?=^[ \t]*\[[^\n\]]+\][ \t]*\n|\Z)"
    )
    return pattern.sub("", text)


def render(base: str, fragment: str, restricted_user: str) -> str:
    if re.fullmatch(r"[a-z_][a-z0-9_-]*", restricted_user) is None:
        raise SystemExit("Restricted Samba username contains unsupported characters")

    validate_physical_syntax(base, "base config")
    reject_dynamic_sources(base, "base config")
    validate_fragment(fragment, restricted_user)

    text = enforce_global_policy(base)
    text = text.replace(
        "\n# EdSys Courier read-only Explorer access. Credential is stored outside Git.\n",
        "\n",
    )
    text = remove_managed_share_sections(text)
    if section_matches(text, TARGET_SECTION):
        raise SystemExit(f"Unable to remove every stale [{TARGET_SECTION}] section")
    assert_unique_sections(text)

    section_names = [
        match.group("name").strip() for match in SECTION_PATTERN.finditer(text)
    ]
    for section in section_names:
        if normalize_section_key(section) != "global":
            text = ensure_section_list_member(
                text, section, "invalid users", restricted_user
            )

    rendered = text.rstrip() + "\n\n" + fragment.strip() + "\n"
    validate_physical_syntax(rendered, "rendered config")
    assert_unique_sections(rendered, "rendered config")
    return rendered


def validate_effective_config(path: Path, restricted_user: str) -> None:
    testparm = shutil.which("testparm")
    if testparm is None:
        raise SystemExit("testparm is required for effective Samba validation")
    result = subprocess.run(
        [testparm, "-sv", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit("testparm rejected the rendered Samba configuration")
    if re.search(r"(?i)unknown parameter|ignoring unknown parameter", result.stderr):
        raise SystemExit("testparm reported an unknown Samba parameter")

    effective = result.stdout
    # testparm represents the literal default winbind separator as a trailing
    # backslash, so continuation rejection applies to source input only.
    validate_physical_syntax(effective, "effective config", reject_continuations=False)
    assert_unique_sections(effective, "effective config")
    section_names = [
        match.group("name").strip() for match in SECTION_PATTERN.finditer(effective)
    ]
    global_name = next(
        (name for name in section_names if normalize_section_key(name) == "global"),
        None,
    )
    target_name = next(
        (
            name
            for name in section_names
            if name.casefold() == TARGET_SECTION.casefold()
        ),
        None,
    )
    if global_name is None or target_name is None:
        raise SystemExit("Effective Samba configuration is missing a managed section")

    def query(section: str, option: str) -> str:
        query_result = subprocess.run(
            [
                testparm,
                "-s",
                f"--section-name={section}",
                f"--parameter-name={option}",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if query_result.returncode != 0:
            raise SystemExit(
                f"Unable to query effective Samba option [{section}] {option}"
            )
        return query_result.stdout.strip()

    expected_global = {
        "interfaces": "lo enp7s0",
        "bind interfaces only": "Yes",
        "hosts allow": "127.0.0.1 192.168.50.0/24",
        "hosts deny": "0.0.0.0/0",
        "config backend": "file",
        "registry shares": "No",
        "usershare max shares": "0",
        "default service": "",
        "auto services": "",
        "load printers": "No",
        "server role": "standalone server",
        "security": "USER",
        "passdb backend": "tdbsam",
        "username map": "",
        "username map script": "",
        "root directory": "",
        "smb ports": "445",
    }
    for option, expected in expected_global.items():
        actual = query(global_name, option)
        if actual.casefold() != expected.casefold():
            raise SystemExit(
                f"Effective [global] {option} is {actual!r}, expected {expected!r}"
            )

    if query(target_name, "path") != EXPECTED_PATH:
        raise SystemExit(f"Effective [{TARGET_SECTION}] path is unsafe")
    if query(target_name, "force user") != EXPECTED_FORCE_USER:
        raise SystemExit(f"Effective [{TARGET_SECTION}] force user is unsafe")
    valid_users = query(target_name, "valid users")
    if valid_users != f"{EXPECTED_FORCE_USER} {restricted_user}":
        raise SystemExit(
            f"Effective [{TARGET_SECTION}] valid users must contain only "
            f"{EXPECTED_FORCE_USER} and {restricted_user}"
        )
    if query(target_name, "invalid users"):
        raise SystemExit(f"Effective [{TARGET_SECTION}] invalid users must be empty")
    encryption = query(target_name, "server smb encrypt")
    if encryption.casefold() != "required":
        raise SystemExit(f"Effective [{TARGET_SECTION}] does not require encryption")
    expected_target = {
        "guest ok": "No",
        "guest only": "No",
        "available": "Yes",
        "browseable": "Yes",
        "printable": "No",
        "max connections": "0",
        "hosts allow": "127.0.0.1 192.168.50.0/24",
        "hosts deny": "0.0.0.0/0",
        "server addresses": "",
        "msdfs root": "No",
        "msdfs proxy": "",
        "magic script": "",
        "magic output": "",
        "read only": "No",
        "follow symlinks": "No",
        "wide links": "No",
        "root preexec": EXPECTED_PREEXEC,
        "root preexec close": "Yes",
        "create mask": "0660",
        "force create mode": "0660",
        "directory mask": "0770",
        "force directory mode": "0770",
        "store dos attributes": "Yes",
        "admin users": "",
        "force group": "",
        "read list": "",
        "write list": "",
        "veto files": "",
        "vfs objects": "",
        "preexec": "",
        "postexec": "",
        "root postexec": "",
    }
    for option, expected in expected_target.items():
        actual = query(target_name, option)
        if actual.casefold() != expected.casefold():
            raise SystemExit(
                f"Effective [{TARGET_SECTION}] {option} is {actual!r}, "
                f"expected {expected!r}"
            )

    for name in section_names:
        if normalize_section_key(name) in {"global", TARGET_SECTION.casefold()}:
            continue
        invalid_users = query(name, "invalid users")
        if not has_plain_terminal_list_member(invalid_users, restricted_user):
            raise SystemExit(f"Effective [{name}] does not deny {restricted_user}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_config", type=Path)
    parser.add_argument("share_fragment", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--restricted-user", required=True)
    args = parser.parse_args()

    rendered = render(
        args.base_config.read_text(encoding="utf-8"),
        args.share_fragment.read_text(encoding="utf-8"),
        args.restricted_user,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}."
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        validate_effective_config(temporary, args.restricted_user)
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
