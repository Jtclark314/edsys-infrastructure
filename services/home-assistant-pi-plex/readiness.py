"""Pure, target-scoped readiness repair for the existing bedroom dashboards.

This transforms the latest supplied dashboard; it does not rebuild views, call
Home Assistant, infer an entity binding, or wake/restart anything.
"""
import copy
import re


NOT_READY = ("off", "unknown", "unavailable")
OWNED_VIEWS = {
    "edsys-media": frozenset(("media", "master-bedroom", "bedroom-pi")),
    "edsys-pi": frozenset(("player", "music-books", "night", "screen", "household", "lg-remote", "capabilities")),
}
PI_SERVICES = frozenset((
    "media_player.media_play", "media_player.media_pause", "media_player.media_stop",
    "media_player.media_play_pause", "media_player.media_seek",
    "media_player.media_next_track", "media_player.media_previous_track",
))
PI_METHODS = frozenset((
    "Input.Home", "Input.Up", "Input.Down", "Input.Left", "Input.Right",
    "Input.Select", "Input.Back", "Input.Info", "Input.ContextMenu",
    "Input.ExecuteAction", "GUI.ActivateWindow", "Addons.ExecuteAddon",
))
TV_SERVICES = frozenset((
    "media_player.volume_up", "media_player.volume_down", "media_player.volume_set",
    "media_player.volume_mute", "media_player.select_source",
))
TV_FEATURES = frozenset((
    "media-player-volume-slider", "media-player-volume-buttons", "media-player-source",
))
TV_COMMANDS = frozenset((
    "tv/switchInput", "audio/volumeUp", "audio/volumeDown", "audio/setVolume", "audio/setMute",
))
PI_MESSAGE = (
    "**Pi connection unavailable.** Home Assistant reports the Pi as off, unknown or unavailable. "
    "Kodi may be stopped or its connection may need attention. Wake TV controls only the LG; "
    "it does not restart the Pi. The LG remote link remains available."
)
TV_MESSAGE = (
    "**TV controls unavailable.** Home Assistant reports the LG as off, unknown or unavailable. "
    "Wake TV remains available where shown. If the TV is already on, check its Home Assistant "
    "connection. Pi playback can continue independently."
)
PI_BROWSE_MESSAGE = (
    "Open Plex, then use these Pi arrows and OK to browse Kodi / Plex on HDMI 1. "
    "The LG remote controls TV menus and native TV apps."
)
TV_REMOTE_MESSAGE = (
    "This remote controls LG TV menus and native TV apps. For Kodi / Plex on HDMI 1, "
    "use the Pi Player arrows and playback controls. Wake the TV first if it is off."
)


def _entity(value):
    if not isinstance(value, str) or not re.fullmatch(r"media_player\.[a-z0-9_]+", value):
        raise ValueError("An explicit media player entity is required")
    return value


def _values(value):
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return set(value)
    return set()


def ready(card, entity):
    return {"type": "conditional", "conditions": [
        {"condition": "state", "entity": entity, "state_not": list(NOT_READY)}
    ], "card": card}


def disconnected_status(entity, *, player):
    return {"type": "conditional", "conditions": [
        {"condition": "state", "entity": entity, "state": list(NOT_READY)}
    ], "card": {"type": "markdown", "content": PI_MESSAGE if player else TV_MESSAGE}}


def _targets(action, entity):
    """Match only an exact entity target, including the older service_data form."""
    targets = []
    target = action.get("target")
    if isinstance(target, dict):
        # Do not change a call that may also address another device/area/label.
        if set(target) - {"entity_id"}:
            return False
        if "entity_id" in target:
            targets.append(_values(target["entity_id"]))
    data = action.get("data", action.get("service_data", {}))
    if isinstance(data, dict) and any(key in data for key in ("device_id", "area_id", "label_id")):
        return False
    if isinstance(data, dict) and "entity_id" in data:
        targets.append(_values(data["entity_id"]))
    return bool(targets) and all(value == {entity} for value in targets)


def _control(node, player, tv):
    if node.get("type") == "media-control" and node.get("entity") == player:
        return player
    if (node.get("type") == "tile" and node.get("entity") == tv
            and any(isinstance(f, dict) and f.get("type") in TV_FEATURES
                    for f in node.get("features", []))):
        return tv
    if node.get("type") != "button":
        return None
    action = node.get("tap_action", {})
    if not isinstance(action, dict) or action.get("action") not in ("perform-action", "call-service"):
        return None
    service = action.get("perform_action", action.get("service"))
    data = action.get("data", action.get("service_data", {}))
    if not isinstance(data, dict):
        return None
    if _targets(action, player) and (service in PI_SERVICES or (
            service == "kodi.call_method" and data.get("method") in PI_METHODS)):
        return player
    if _targets(action, tv) and (service in TV_SERVICES or (
            service == "webostv.button" and data.get("button") in ("VOLUMEUP", "VOLUMEDOWN", "MUTE")) or (
            service == "webostv.command" and data.get("command") in TV_COMMANDS)):
        return tv
    return None


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _blocked(conditions, inherited):
    result = {entity: set(states) for entity, states in inherited.items()}
    for condition in conditions:
        if not isinstance(condition, dict) or condition.get("condition") != "state":
            continue
        if condition.get("attribute"):
            continue
        entity = condition.get("entity_id") or condition.get("entity")
        if entity not in result:
            continue
        if condition.get("state") is not None:
            allowed = _values(condition["state"])
            # Native conditions can expand another entity ID to its current state.
            if allowed and not any(re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", value) for value in allowed):
                result[entity].update(set(NOT_READY) - allowed)
        else:
            result[entity].update(_values(condition.get("state_not")) & set(NOT_READY))
    return result


def _rewrite_text(node, endpoints, player, tv):
    replacements = {}
    if player in endpoints:
        replacements["Open Plex, then use the arrows and OK to choose something on the TV."] = PI_BROWSE_MESSAGE
    if tv in endpoints:
        replacements["Navigation and playback control the TV’s current app. Wake the TV first if it is off."] = TV_REMOTE_MESSAGE
    changed = 0
    if node.get("type") == "markdown" and node.get("content") in replacements:
        node["content"] = replacements[node["content"]]
        changed += 1
    if player in endpoints and node.get("type") == "heading" and node.get("heading") == "Browse on the TV":
        node["heading"] = "Browse Pi Plex / Kodi"
        changed += 1
    action = node.get("tap_action", {})
    if (node.get("type") == "button" and isinstance(action, dict)
            and action.get("action") == "navigate"
            and action.get("navigation_path") in ("/edsys-pi/lg-remote", "/edsys-media/master-bedroom")
            and node.get("name") in ("LG TV remote", "Full LG remote")):
        node["name"] = "LG TV apps & remote"
        changed += 1
    return changed


def repair_view(original, player, tv):
    """Repair one explicitly selected owned view without changing action payloads."""
    _entity(player); _entity(tv)
    if player == tv:
        raise ValueError("Pi and LG must have distinct reviewed identities")
    result = copy.deepcopy(original)
    counts = {"pi_guards_updated": 0, "pi_controls_guarded": 0, "tv_controls_guarded": 0,
              "status_cards_added": 0, "wording_updated": 0}

    def patch(node, inherited, text_endpoints=frozenset()):
        if isinstance(node, list):
            return [patch(child, inherited, text_endpoints) for child in node]
        if not isinstance(node, dict):
            return node
        if isinstance(node.get("cards"), list):
            text_endpoints = {_control(child, player, tv) for child in _walk(node["cards"])}
        counts["wording_updated"] += _rewrite_text(node, text_endpoints, player, tv)
        blocked = inherited
        if node.get("type") == "conditional":
            conditions = node.get("conditions", [])
            contains_pi = any(_control(child, player, tv) == player for child in _walk(node.get("card")))
            if contains_pi:
                for condition in conditions:
                    if (isinstance(condition, dict) and condition.get("condition") == "state"
                            and not condition.get("attribute") and condition.get("state") is None
                            and (condition.get("entity_id") or condition.get("entity")) == player
                            and _values(condition.get("state_not")) == {"unknown", "unavailable"}):
                        condition["state_not"] = list(NOT_READY)
                        counts["pi_guards_updated"] += 1
            blocked = _blocked(conditions, inherited)
        for key, child in list(node.items()):
            if key != "conditions":
                node[key] = patch(child, blocked, text_endpoints)
        endpoint = _control(node, player, tv)
        if endpoint and not set(NOT_READY) <= blocked[endpoint]:
            counts["pi_controls_guarded" if endpoint == player else "tv_controls_guarded"] += 1
            return ready(node, endpoint)
        return node

    result = patch(result, {player: set(), tv: set()})
    endpoints = {_control(node, player, tv) for node in _walk(result)}
    statuses = []
    for endpoint in (player, tv):
        if endpoint not in endpoints:
            continue
        status = disconnected_status(endpoint, player=endpoint == player)
        if not any(node == status for node in _walk(result)):
            statuses.append(status)
    if statuses:
        # Keep status in an existing card container; preserve all prior card order.
        sections = result.get("sections")
        if isinstance(sections, list) and sections and isinstance(sections[0].get("cards"), list):
            sections[0]["cards"].extend(statuses)
        elif isinstance(result.get("cards"), list):
            result["cards"].extend(statuses)
        else:
            raise ValueError("Owned view has no supported status-card container")
        counts["status_cards_added"] = len(statuses)
    return result, counts


def repair_dashboard(original, player, tv, *, dashboard):
    """Transform current Media/Pi config, limiting changes to known owned views."""
    _entity(player); _entity(tv)
    if dashboard not in OWNED_VIEWS:
        raise ValueError("Only the reviewed Media or Pi dashboard is supported")
    result = copy.deepcopy(original)
    counts = {"views_changed": 0, "pi_guards_updated": 0, "pi_controls_guarded": 0,
              "tv_controls_guarded": 0, "status_cards_added": 0, "wording_updated": 0}
    if not isinstance(result.get("views"), list):
        raise ValueError("Dashboard views must be a list")
    for index, view in enumerate(result["views"]):
        if not isinstance(view, dict) or view.get("path") not in OWNED_VIEWS[dashboard]:
            continue
        changed, view_counts = repair_view(view, player, tv)
        result["views"][index] = changed
        counts["views_changed"] += changed != view
        for key, value in view_counts.items():
            counts[key] += value
    return result, counts
