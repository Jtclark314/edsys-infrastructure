"""Native bedroom command dashboard, built from supplied live HA observations.

No entity IDs, device bindings, app routes or household data are embedded here.
Existing dashboards are read as sources and are never modified by this module.
"""
from copy import deepcopy
from collections import defaultdict

URL = "edsys-master-bedroom"


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def heading(text, icon):
    return {"type": "heading", "heading": text, "heading_style": "title", "icon": "mdi:" + icon}


def section(title, icon, cards):
    return {"type": "grid", "cards": [heading(title, icon), *cards]}


def note(content):
    return {"type": "markdown", "content": content}


def nav(title, icon, path):
    return {"type": "button", "name": title, "icon": "mdi:" + icon,
            "show_state": False, "tap_action": {"action": "navigate", "navigation_path": path},
            "hold_action": {"action": "none"}, "double_tap_action": {"action": "none"}}


def grid(cards, columns=2):
    return {"type": "grid", "columns": columns, "square": False, "cards": cards}


def view(title, path, icon, sections, **extra):
    return {"title": title, "path": path, "icon": "mdi:" + icon, "type": "sections",
            "max_columns": 3, "sections": sections, **extra}


def rewrite(value, routes):
    if isinstance(value, dict):
        return {key: rewrite(child, routes) for key, child in value.items()}
    if isinstance(value, list):
        return [rewrite(child, routes) for child in value]
    if isinstance(value, str):
        for old in sorted(routes, key=len, reverse=True):
            value = value.replace(old, routes[old])
    return value


def one(values, predicate, label):
    result = [value for value in values if predicate(value)]
    if len(result) != 1:
        raise ValueError(f"Expected one verified {label}; got {len(result)}")
    return result[0]


def build(observation):
    states = {row["entity_id"]: row for row in observation["states"]}
    registry = {row["ei"]: row for row in observation["registry"]["entities"]}
    areas = {row["entity_id"]: row["area"] for row in observation["areas"]}
    pi = observation["pi"]
    media = observation["media"]
    bindings = observation["bindings"]
    tv, player, plex = (bindings[key] for key in ("tv", "player", "plex"))
    for entity in (tv, player, plex):
        if entity not in states:
            raise ValueError("A reviewed media binding is absent from live HA states")

    base = "/" + URL + "/"
    routes = {"/edsys-pi/" + v["path"]: base + "pi-" + v["path"] for v in pi["views"]}
    routes["/edsys-media/master-bedroom"] = base + "tv"
    routes["/edsys-media/bedroom-pi"] = base + "pi-player"
    clones = []
    for original in pi["views"]:
        clone = rewrite(deepcopy(original), routes)
        clone["path"] = "pi-" + original["path"]
        clone["subview"] = original["path"] != "player"
        clone["back_path"] = base + "home"
        if original["path"] == "player":
            clone["title"] = "Pi player"
        clone.setdefault("sections", []).append(section("Master Bedroom", "bed-king-outline", [
            nav("Command centre", "view-dashboard", base + "home"),
            nav("All lights & switches", "lightbulb-group", base + "lights")]))
        clones.append(clone)
    remote = rewrite(deepcopy(one(media["views"], lambda v: v.get("path") == "master-bedroom", "LG remote view")), routes)
    remote.update(title="TV remote", path="tv", subview=False)

    switches = sorted(entity for entity in states if entity.startswith(("light.", "switch.")))
    everyday = [entity for entity in switches if registry.get(entity, {}).get("ec") is None]
    settings = [entity for entity in switches if entity not in everyday]

    def controls(entities):
        grouped = defaultdict(list)
        for entity in entities:
            grouped[areas.get(entity) or "Other devices"].append(entity)
        return [section(area, "lightbulb-group", [{
            "type": "entities", "show_header_toggle": False,
            "entities": sorted(entities, key=lambda e: states[e]["attributes"].get("friendly_name", e).lower())
        }]) for area, entities in sorted(grouped.items())]

    lights = view("Lights & switches", "lights", "lightbulb-group", [
        section("Whole-house controls", "home-lightbulb", [
            note(f"**{sum(e.startswith('light.') for e in switches)} lights · "
                 f"{sum(e.startswith('switch.') for e in switches)} switches**\n\n"
                 "Room lights and outlets come first. Camera, cloud and device settings are below. "
                 "Unavailable devices remain listed so their connection state is visible."),
            {"type": "entity-filter", "entities": switches, "state_filter": ["unavailable", "unknown"],
             "show_empty": False, "card": {"type": "entities", "title": "Connections needing attention", "show_header_toggle": False}}
        ]), *controls(everyday),
        section("Device & camera settings", "tune", [note(
            "These switches change recording, detection, privacy, cloud access or device settings. "
            "Each control acts on its named device.")]), *controls(settings)])

    pipeline = one(observation["pipelines"]["pipelines"],
                   lambda p: p["conversation_engine"] == bindings["voice"] and p["name"] == bindings["pipeline_name"],
                   "production voice pipeline")
    assist = {"type": "button", "name": "Talk or type to EdSys", "icon": "mdi:assistant",
              "tap_action": {"action": "assist", "pipeline_id": pipeline["id"], "start_listening": False}}
    voice_entities = [e for e in [bindings["voice"], pipeline.get("stt_engine"), pipeline.get("tts_engine"),
                                 bindings.get("wake"), *bindings["status"]] if e in states]
    voice = view("Voice & status", "voice", "assistant", [
        section("EdSys voice", "assistant", [assist, note(
            "Open Assist to type or use the microphone. Microphone recording needs HTTPS or the Home Assistant app. "
            "On Nimo, **Alt+V** keeps the existing voice workflow."),
            {"type": "entities", "title": "Voice services", "show_header_toggle": False,
             "entities": [{"entity": e, "type": "simple-entity"} for e in voice_entities]},
            note("**EdSys backend:** {{ 'Ready' if state_attr('" + bindings["voice"] +
                 "', 'backend_ready') else 'Not ready' }}\n\n"
                 "Service state shows availability and last activity; it does not prove microphone or speaker operation.")]),
        section("Pi presentation", "monitor-dashboard", [
            nav("TV screen & cameras", "monitor-dashboard", base + "pi-screen"),
            nav("Night, sleep timer & artwork", "weather-night", base + "pi-night"),
            nav("Pi capabilities & limits", "information-outline", base + "pi-capabilities")]),
        section("More at home", "home-heart", [
            nav("Family Hub", "home-heart", bindings["family_path"]),
            nav("Weather", "weather-partly-cloudy", bindings["weather_path"]),
            nav("Cameras", "cctv", bindings["cameras_path"]),
            nav("Original Bedroom Pi", "raspberry-pi", "/edsys-pi/player")])])

    bedroom_lights = [e for e in everyday if areas.get(e) == bindings["area"]]
    bedside = [{"type": "tile", "entity": e, "name": states[e]["attributes"].get("friendly_name", e)}
               for e in bedroom_lights]
    music = [deepcopy(n) for n in walk(pi) if n.get("type") == "shortcut" and n.get("label") == "Music Assistant"]
    if not music:
        music = [deepcopy(n) for n in walk(pi) if n.get("type") == "shortcut" and "Music Assistant" in str(n)]
    home = view("Home", "home", "bed-king-outline", [
        section("Master Bedroom", "bed-king-outline", [
            note("Your bedroom command centre. Choose the TV remote for LG apps, "
                 "or Pi player for Plex and YouTube on HDMI 1."),
            grid([nav("LG TV remote", "remote-tv", base + "tv"), nav("Pi player / Plex", "plex", base + "pi-player")]),
            {"type": "media-control", "entity": tv},
            {"type": "media-control", "entity": plex}]),
        section("Bedroom Pi & apps", "raspberry-pi", [
            {"type": "media-control", "entity": player},
            grid([nav("YouTube", "youtube", base + "pi-youtube"), nav("Music & audiobooks", "music-box-multiple", base + "pi-music-books"),
                  nav("Night & sleep", "weather-night", base + "pi-night"), nav("TV screen & cameras", "monitor-dashboard", base + "pi-screen")]),
            *music[:1], nav("Family, reminders & weather on TV", "home-heart", base + "pi-household")]),
        section("Lights, voice & home", "home-lightbulb", [*bedside,
            nav("Every light & switch", "lightbulb-group", base + "lights"), assist,
            nav("Voice & Pi status", "assistant", base + "voice"),
            nav("Family Hub", "home-heart", bindings["family_path"]),
            nav("Weather", "weather-partly-cloudy", bindings["weather_path"])])])
    result = {"title": "Master Bedroom", "views": [home, remote, clones[0], lights, voice, *clones[1:]]}
    validate(result, states, observation["services"], observation["dashboards"])
    return result


def validate(dashboard, states, services, dashboards):
    known_services = {s["domain"] + "." + name for s in services for name in s["services"]}
    paths = {"/" + URL + "/" + v["path"] for v in dashboard["views"]}
    paths.update("/" + url + "/" + v.get("path", str(i))
                 for url, config in dashboards.items() for i, v in enumerate(config.get("views", [])))
    for node in walk(dashboard):
        if str(node.get("type", "")).startswith("custom:"):
            raise ValueError("The new dashboard must use native HA cards")
        for key in ("entity", "entity_id"):
            values = node.get(key, [])
            for entity in ([values] if isinstance(values, str) else values if isinstance(values, list) else []):
                if entity not in states:
                    raise ValueError("Missing entity: " + entity)
        if node.get("perform_action", node.get("service")):
            service = node.get("perform_action", node.get("service"))
            if service not in known_services:
                raise ValueError("Missing service: " + service)
        if node.get("action") == "navigate":
            path = node["navigation_path"]
            if path.startswith("/edsys-") and path not in paths:
                raise ValueError("Missing view route: " + path)
    expected = {e for e in states if e.startswith(("light.", "switch."))}
    light_view = one(dashboard["views"], lambda v: v["path"] == "lights", "whole-house view")
    actual = {e for n in walk(light_view) if n.get("type") == "entities" for e in n.get("entities", []) if isinstance(e, str)}
    if actual != expected:
        raise ValueError("Whole-house light/switch control coverage differs from live inventory")
