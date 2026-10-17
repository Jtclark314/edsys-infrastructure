"""Add bounded weather presentation controls to the latest native Pi dashboard.

Pure transformation only. The existing screen service owns permission, Pi/HDMI
selection and rendering checks; no button powers the TV or selects its input.
"""
from copy import deepcopy


SCREEN_SERVICE = "edsys_voice_knowledge.bedroom_screen"
TARGETS = {"player": "On the TV", "screen": "TV screen controls", "household": "Bedroom"}
WEATHER_NOTE = (
    "Weather on the LG: the TV must already show the Pi on **HDMI 1**. "
    "Forecast closes after **90 seconds**. Regional **NOAA KGSP** radar uses a "
    "time-stamped image loop and closes after **180 seconds**; read the frame time. "
    "**Back** or **Dismiss popup** closes either card sooner. "
    "TV power/input and playback stay unchanged."
)
PENDING_TABLE = (
    "| Weather capability | Screen status |\n| --- | --- |\n"
    "| Weather forecast on TV | Screen acceptance pending |\n"
    "| Regional NOAA KGSP radar on TV | Screen acceptance pending |"
)
OLD_HOUSEHOLD = (
    "Door, temperature and weather sources are not configured. "
    "The overview shows the devices that are available."
)
NEW_HOUSEHOLD = (
    "Door and room-temperature sources are not configured. "
    "Weather TV controls have screen acceptance pending. "
    "The overview shows the devices that are available."
)
OLD_PLANNED = (
    "Voice-synchronised animation and captions, AI visual answers, full-motion "
    "camera inset, weather, real door/temperature readings, personal photo "
    "sources, and automatic problem notifications need further setup or qualification."
)
NEW_PLANNED = OLD_PLANNED.replace("camera inset, weather,", "camera inset,")


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _screen_action(card, action):
    return any(
        obj.get("action") == "perform-action"
        and obj.get("perform_action") == SCREEN_SERVICE
        and isinstance(obj.get("data"), dict)
        and obj["data"].get("action") == action
        for obj in _walk(card)
    )


def _button(name, icon, action, duration=None):
    data = {"action": action}
    if duration is not None:
        data["duration"] = duration
    return {"type": "button", "name": name, "icon": "mdi:" + icon,
            "icon_height": "30px", "show_state": False,
            "tap_action": {"action": "perform-action", "perform_action": SCREEN_SERVICE,
                           "data": data},
            "hold_action": {"action": "none"}, "double_tap_action": {"action": "none"}}


def _grid(cards):
    return {"type": "grid", "columns": len(cards), "square": False, "cards": cards}


def _one_view(dashboard, path):
    views = [view for view in dashboard.get("views", [])
             if isinstance(view, dict) and view.get("path") == path]
    if len(views) != 1:
        raise ValueError("Expected one existing Pi view: " + path)
    return views[0]


def _one_section(view, title):
    sections = [section for section in view.get("sections", [])
                if isinstance(section, dict) and any(
                    isinstance(card, dict) and card.get("type") == "heading"
                    and card.get("heading") == title for card in section.get("cards", []))]
    if len(sections) != 1:
        raise ValueError("Expected one existing Pi section: " + title)
    return sections[0]


def _update_known_notes(value):
    for card in _walk(value):
        if card.get("type") == "markdown" and isinstance(card.get("content"), str):
            card["content"] = card["content"].replace(
                OLD_HOUSEHOLD, NEW_HOUSEHOLD).replace(OLD_PLANNED, NEW_PLANNED)


def extend(original: dict) -> dict:
    """Deep-copy, narrowly extend, and return a dashboard; repeat calls are stable."""
    if not isinstance(original, dict):
        raise ValueError("Expected a dashboard object")
    result = deepcopy(original)
    targets = {path: _one_section(_one_view(result, path), title)
               for path, title in TARGETS.items()}
    capabilities = _one_view(result, "capabilities")

    for path, section in targets.items():
        cards = section["cards"]
        buttons = []
        for action, name, icon, duration in (
            ("weather_forecast", "Weather forecast · on TV", "weather-partly-cloudy", 90),
            ("weather_radar", "Weather radar · on TV", "radar", 180),
        ):
            existing = [obj for obj in _walk(section)
                        if obj.get("action") == "perform-action"
                        and obj.get("perform_action") == SCREEN_SERVICE
                        and isinstance(obj.get("data"), dict)
                        and obj["data"].get("action") == action]
            if existing:
                if len(existing) != 1 or existing[0]["data"].get("duration") != duration:
                    raise ValueError("Conflicting existing weather screen control: " + path)
            else:
                buttons.append(_button(name, icon, action, duration))
        if buttons:
            dismiss = next((index for index, card in enumerate(cards)
                            if _screen_action(card, "clear")), None)
            if dismiss is None:
                buttons.append(_button("Dismiss popup", "close", "clear"))
                insert = next(index for index, card in enumerate(cards)
                              if card.get("type") == "heading") + 1
            else:
                insert = dismiss + 1
            cards.insert(insert, _grid(buttons))
        if not any(card.get("type") == "markdown" and isinstance(card.get("content"), str)
                   and card["content"].startswith("Weather on the LG:") for card in cards):
            cards.append({"type": "markdown", "content": WEATHER_NOTE})
        if path == "screen":
            links = []
            for name, icon, destination in (
                ("Weather · on this device", "weather-partly-cloudy", "/edsys-weather/overview"),
                ("Radar · on this device", "radar", "/edsys-weather/radar"),
            ):
                if any(obj.get("action") == "navigate"
                       and obj.get("navigation_path") == destination for obj in _walk(section)):
                    continue
                links.append({"type": "button", "name": name, "icon": "mdi:" + icon,
                              "tap_action": {"action": "navigate", "navigation_path": destination},
                              "hold_action": {"action": "none"},
                              "double_tap_action": {"action": "none"}})
            if links:
                cards.append(_grid(links))

    _update_known_notes(targets["household"])
    _update_known_notes(capabilities)
    if not any(card.get("type") == "markdown" and isinstance(card.get("content"), str)
               and card["content"].startswith("| Weather capability |")
               for card in _walk(capabilities)):
        capabilities["sections"].append({"type": "grid", "cards": [
            {"type": "heading", "heading": "Weather · screen acceptance pending",
             "heading_style": "title", "icon": "mdi:weather-partly-cloudy"},
            {"type": "markdown", "content": PENDING_TABLE},
        ]})
    return result
