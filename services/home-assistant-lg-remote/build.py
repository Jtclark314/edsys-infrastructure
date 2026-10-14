"""Native Lovelace remote; runtime entity IDs and dashboards stay private."""
import copy
import re


def hdmi1_button(entity):
    """Select physical HDMI 1 independently of LG's mutable source label."""
    if not re.fullmatch(r"media_player\.[a-z0-9_]+", entity):
        raise ValueError("An explicit TV entity is required")
    return {"type": "button", "name": "HDMI 1 · Pi", "icon": "mdi:video-input-hdmi",
            "icon_height": "24px", "show_state": False,
            "tap_action": {"action": "perform-action", "perform_action": "webostv.command",
                           "data": {"entity_id": entity, "command": "tv/switchInput",
                                    "payload": {"inputId": "HDMI_1"}}},
            "hold_action": {"action": "none"}, "double_tap_action": {"action": "none"}}


def build_sections(entity, sources):
    if not re.fullmatch(r"media_player\.[a-z0-9_]+", entity):
        raise ValueError("An explicit TV entity is required")

    def action(name, icon, service, **data):
        return {"type": "button", "name": name, "icon": "mdi:" + icon,
                "icon_height": "24px", "show_state": False,
                "tap_action": {"action": "perform-action", "perform_action": service,
                               "data": {"entity_id": entity, **data}},
                "hold_action": {"action": "none"}, "double_tap_action": {"action": "none"}}

    def key(name, icon, button):
        return action(name, icon, "webostv.button", button=button)

    def grid(cards, columns=3):
        return {"type": "grid", "columns": columns, "square": False, "cards": cards}

    def ready(card):
        return {"type": "conditional", "conditions": [
            {"condition": "state", "entity": entity, "state_not": state}
            for state in ("off", "unknown", "unavailable")], "card": card}

    direction = grid([key("Home", "home", "HOME"), key("Up", "chevron-up", "UP"), key("Settings", "cog", "MENU"),
                      key("Left", "chevron-left", "LEFT"), key("OK", "checkbox-marked-circle", "ENTER"), key("Right", "chevron-right", "RIGHT"),
                      key("Back", "arrow-u-left-top", "BACK"), key("Down", "chevron-down", "DOWN"), key("Exit", "close", "EXIT")])
    volume = grid([key("Volume −", "volume-minus", "VOLUMEDOWN"), key("Mute", "volume-mute", "MUTE"), key("Volume +", "volume-plus", "VOLUMEUP")])
    playback = grid([
        action("Rewind", "rewind", "webostv.command", command="media.controls/rewind"),
        action("Play", "play", "media_player.media_play"),
        action("Fast forward", "fast-forward", "webostv.command", command="media.controls/fastForward"),
        action("Stop", "stop", "media_player.media_stop"), action("Pause", "pause", "media_player.media_pause"),
        key("Info", "information-outline", "INFO")])
    shortcuts = [action(name, "plex" if name == "Plex" else "apps", "media_player.select_source", source=name)
                 for name in ("Plex", "Netflix", "YouTube", "Prime Video", "Live TV") if name in sources]
    shortcuts.append(hdmi1_button(entity))
    primary = {"type": "grid", "cards": [
        {"type": "heading", "heading": "Master Bedroom remote", "icon": "mdi:remote-tv", "heading_style": "title"},
        grid([action("Wake TV", "power", "media_player.turn_on"), action("TV off", "power-standby", "media_player.turn_off")], 2),
        {"type": "markdown", "text_only": True, "content": "Navigation and playback control the TV’s current app. Wake the TV first if it is off."},
        ready({"type": "vertical-stack", "cards": [direction, volume, playback]})]}
    if shortcuts:
        primary["cards"].append(ready(grid(shortcuts)))
    numbers = [key(str(n), "numeric-" + str(n), str(n)) for n in range(1, 10)]
    numbers += [key("Dash", "minus", "DASH"), key("0", "numeric-0", "0"), key("Enter", "keyboard-return", "ENTER")]
    secondary = {"type": "grid", "cards": [
        {"type": "heading", "heading": "Number pad & TV functions", "icon": "mdi:dialpad", "heading_style": "title"},
        ready({"type": "vertical-stack", "cards": [grid(numbers),
            grid([key("Channel −", "minus", "CHANNELDOWN"), key("Guide", "television-guide", "GUIDE"), key("Channel +", "plus", "CHANNELUP")]),
            grid([key("Captions", "closed-caption", "CC"), key("Red", "alpha-r-circle", "RED"), key("Green", "alpha-g-circle", "GREEN"),
                  key("Yellow", "alpha-y-circle", "YELLOW"), key("Blue", "alpha-b-circle", "BLUE"), key("Options", "asterisk", "ASTERISK")])]}),
        {"type": "markdown", "text_only": True, "content": "Number keys and channel functions depend on the active app or Live TV. Apps & inputs and the separate Plex session controls are retained alongside this remote."}]}
    return primary, secondary


def update_dashboard(original, entity, sources):
    updated = copy.deepcopy(original)
    matches = [v for v in updated["views"] if v.get("path") == "master-bedroom"]
    if len(matches) != 1:
        raise ValueError("Expected the existing Master Bedroom view")
    view = matches[0]
    if any(card.get("heading") == "Master Bedroom remote" for section in view.get("sections", []) for card in section.get("cards", [])):
        raise ValueError("Remote already exists; inspect before replacing")
    primary, secondary = build_sections(entity, sources)
    old = view["sections"]
    view["sections"] = [primary, old[0], secondary, *old[1:]]
    return updated
