"""Pure checks for the deliberately small, owner-reviewed home pilot."""
from __future__ import annotations

import re


class PilotRefusal(ValueError):
    """An ambiguous, unsupported or unsafe pilot request."""


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def contains(text: str, phrase: str) -> bool:
    return f" {normalize(phrase)} " in f" {normalize(text)} "


def check_request(text: str) -> str:
    if not isinstance(text, str) or not 1 <= len(text) <= 512:
        raise PilotRefusal("Please repeat a short, complete home command.")
    q = normalize(text)
    if re.search(r"\b(don t|do not|never|not|unless|if|when|later|tomorrow|seconds?|minutes?|hours?|days?)\b", q):
        raise PilotRefusal("The pilot supports immediate commands. Please state the action directly.")
    if re.search(r"\b(and|then|except)\b", q):
        raise PilotRefusal("Please give one device command at a time during the pilot.")
    return q


def check_control(text: str, target: str, operation: str, value: float | None) -> None:
    q = check_request(text)
    command = re.sub(r"^(?:(?:please|can you|could you|would you|will you) )+", "", q)
    prefixes = {
        "on": r"^(turn|switch|light up|lights? on)\b", "off": r"^(turn|switch|shut|kill|lights? (out|off))\b",
        "mute": r"^(mute|silence)\b", "unmute": r"^(unmute|restore sound)\b",
        "pause": r"^(pause|hold)\b", "resume": r"^(resume|continue|unpause|play)\b",
        "stop": r"^stop\b", "open_plex": r"^(open|launch|bring up)\b", "set_volume": r"^(set|turn|put)\b",
    }
    if operation not in prefixes or not re.search(prefixes[operation], command):
        raise PilotRefusal("Please give a direct device command; questions do not authorize control.")
    if target == "gadget_lab_light":
        if not any(contains(q, area) for area in ("gadget lab", "lab")):
            raise PilotRefusal("Which room's light? The lighting pilot covers Gadget Lab.")
        if re.search(r"\b(desk|downlights|patio|garage|bedroom)\b", q):
            raise PilotRefusal("Gadget Lab lights means only the main light; please name the other light explicitly.")
        if not re.search(r"\b(lights?|lamps?)\b", q):
            raise PilotRefusal("Please specify the Gadget Lab light.")
        allowed = {"on", "off"}
    elif target in ("master_bedroom_tv", "master_bedroom_plex"):
        if not any(contains(q, area) for area in ("master bedroom", "main bedroom", "primary bedroom")):
            raise PilotRefusal("Which bedroom: Master or Secondary? Please include it in the command.")
        if re.search(r"\b(secondary|guest|living|media room|office)\b", q):
            raise PilotRefusal("That request names a room outside the Master Bedroom pilot.")
        if not re.search(r"\b(tv|television|plex)\b", q):
            raise PilotRefusal("Please specify the Master Bedroom TV or Plex.")
        allowed = {"pause", "resume", "stop"} if target.endswith("plex") else {"on", "off", "mute", "unmute", "set_volume", "open_plex"}
    else:
        raise PilotRefusal("That device is outside the reviewed pilot.")
    if operation not in allowed:
        raise PilotRefusal("That action is not supported for the selected pilot device.")
    words = {
        "on": ("on", "light up"), "off": ("off", "lights out", "light out", "kill"),
        "mute": ("mute", "silence"), "unmute": ("unmute", "restore sound"),
        "pause": ("pause", "hold"), "resume": ("resume", "continue", "play", "unpause"),
        "stop": ("stop",), "open_plex": ("open", "launch", "bring up"),
        "set_volume": ("volume",),
    }
    if not any(contains(q, phrase) for phrase in words[operation]):
        raise PilotRefusal("Please state the requested action explicitly.")
    opposing = {"on": ("off", "out"), "off": ("on",), "mute": ("unmute",), "unmute": ("mute",),
                "pause": ("resume", "unpause", "stop"), "resume": ("pause", "stop"), "stop": ("pause", "resume")}
    if any(contains(q, phrase) for phrase in opposing.get(operation, ())):
        raise PilotRefusal("The command contains conflicting actions; please give one action.")
    if operation == "open_plex" and not contains(q, "plex"):
        raise PilotRefusal("Please name Plex when asking to open it.")
    if operation == "resume" and re.search(r"\b(movie|film|episode|season|title)\b", q):
        raise PilotRefusal("This pilot resumes the current Plex session; choosing a new title needs a separate media lookup.")
    if operation == "resume" and command.startswith("play ") and not re.fullmatch(r"play (?:the )?(?:(?:master|main|primary) bedroom (?:tv|television|plex)|plex (?:in|on) (?:the )?(?:master|main|primary) bedroom(?: tv)?)", command):
        raise PilotRefusal("Playing a new title is outside this pilot. Say resume Plex and include the bedroom.")
    if operation == "set_volume":
        numbers = re.findall(r"\b\d{1,3}\b", q)
        if len(numbers) != 1 or value is None or not 0 <= value <= 1 or abs(int(numbers[0]) / 100 - value) > 0.0001:
            raise PilotRefusal("Please specify a single volume percentage from zero to one hundred.")
    elif value is not None:
        raise PilotRefusal("This action does not accept a numeric setting.")


def choose_camera(text: str, cameras: dict, requested: str | None = None) -> str:
    q = check_request(text)
    if not any(contains(q, verb) for verb in ("show", "display", "view", "bring up", "pull up", "open")):
        raise PilotRefusal("Please ask to show a named camera.")
    if re.search(r"\b(tv|television|roku|pi)\b", q):
        raise PilotRefusal("This pilot shows cameras on Nimo. TV display compatibility is not commissioned yet.")
    matches = [key for key, camera in cameras.items() if any(contains(q, alias) for alias in camera["aliases"])]
    # Tracking and primary feeds are separate; choose only an explicitly named one.
    matches = [key for key in matches if ("tracking" in key) == contains(q, "tracking")]
    if len(matches) != 1 or requested is not None and requested != matches[0]:
        raise PilotRefusal("Which camera? Please name its location, such as Front Porch or Back Patio.")
    return matches[0]
