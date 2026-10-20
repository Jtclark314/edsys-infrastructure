"""Pure native YouTube extension for the existing Bedroom Pi HA dashboard.

Supply reviewed private Kodi/LG entity bindings and the latest live dashboard.
This module performs no network call, device action, credential read or save.
Folder routes are from the installed official YouTube 7.4.4 add-on. Account,
Watch later and History remain in its native root because those entries depend
on the active account and its remote/local playlist configuration.
"""

from copy import deepcopy
import re


DASHBOARD = "edsys-pi"
PATH = "youtube"
URL = "/" + DASHBOARD + "/" + PATH
ROOT = "plugin://plugin.video.youtube/"
NOT_READY = ["off", "unknown", "unavailable"]
FOLDERS = (
    ("Open YouTube", "youtube", ROOT),
    ("Search", "magnify", ROOT + "kodion/search/list/"),
    ("New uploads", "play-box-multiple", ROOT + "special/my_subscriptions/"),
    ("Subscribed channels", "account-multiple", ROOT + "subscriptions/list/"),
    ("My playlists", "playlist-play", ROOT + "channel/mine/playlists/"),
    ("Recommendations", "compass-outline", ROOT + "special/recommendations/"),
)


def _entity(value):
    if not isinstance(value, str) or not re.fullmatch(r"media_player\.[a-z0-9_]+", value):
        raise ValueError("An explicit reviewed media_player entity is required")
    return value


def _bindings(player, tv):
    _entity(player)
    _entity(tv)
    if player == tv:
        raise ValueError("Kodi and LG must be distinct reviewed targets")


def _heading(title, icon):
    return {"type": "heading", "heading": title, "heading_style": "title", "icon": "mdi:" + icon}


def _note(text):
    return {"type": "markdown", "content": text}


def _grid(cards, columns=3):
    return {"type": "grid", "columns": columns, "square": False, "cards": cards}


def _button(title, icon, service, entity, **data):
    return {"type": "button", "name": title, "icon": "mdi:" + icon,
            "icon_height": "30px", "show_state": False,
            "tap_action": {"action": "perform-action", "perform_action": service,
                           "target": {"entity_id": entity}, "data": data},
            "hold_action": {"action": "none"}, "double_tap_action": {"action": "none"}}


def _ready(card, entity):
    return {"type": "conditional", "conditions": [
        {"condition": "state", "entity": entity, "state_not": list(NOT_READY)}], "card": card}


def _disconnected(entity, text):
    return {"type": "conditional", "conditions": [
        {"condition": "state", "entity": entity, "state": list(NOT_READY)}], "card": _note(text)}


def _nav(title, icon, path):
    return {"type": "button", "name": title, "icon": "mdi:" + icon,
            "show_state": False,
            "tap_action": {"action": "navigate", "navigation_path": path},
            "hold_action": {"action": "none"}, "double_tap_action": {"action": "none"}}


def link():
    return _nav("YouTube on the Pi", "youtube", URL)


def build_view(player, tv):
    """Build one responsive native view; page loading cannot send an action."""
    _bindings(player, tv)

    def kodi(title, icon, method, **data):
        return _button(title, icon, "kodi.call_method", player, method=method, **data)

    folders = [kodi(title, icon, "GUI.ActivateWindow", window="videos", parameters=[uri, "return"])
               for title, icon, uri in FOLDERS]
    navigation = [
        kodi("Kodi home", "home-outline", "Input.Home"), kodi("Up", "chevron-up", "Input.Up"),
        kodi("Info", "information-outline", "Input.Info"),
        kodi("Left", "chevron-left", "Input.Left"), kodi("OK", "circle-outline", "Input.Select"),
        kodi("Right", "chevron-right", "Input.Right"),
        kodi("Back", "arrow-u-left-top", "Input.Back"), kodi("Down", "chevron-down", "Input.Down"),
        kodi("Options", "dots-horizontal", "Input.ContextMenu"),
    ]
    transport = [
        _button("Play / pause", "play-pause", "media_player.media_play_pause", player),
        _button("Pause", "pause", "media_player.media_pause", player),
        _button("Stop", "stop", "media_player.media_stop", player),
    ]
    return {"title": "YouTube", "path": PATH, "icon": "mdi:youtube", "type": "sections", "max_columns": 3,
            "sections": [
                {"type": "grid", "cards": [
                    _heading("YouTube on the bedroom Pi", "youtube"),
                    _note("Choose **Show Pi · HDMI 1**, then browse YouTube on the TV. "
                          "These shortcuts open Kodi’s YouTube add-on. Selecting a video can replace "
                          "the current Pi playback."),
                    _ready(_grid(folders, 2), player),
                    _ready(kodi("Account, Watch later & History", "account-circle-outline",
                                "GUI.ActivateWindow", window="videos", parameters=[ROOT, "return"]), player),
                    _note("**Search:** open Search, choose **New Search**, then use the on-TV keyboard "
                          "with the arrows and OK. Back closes the keyboard. Account, Watch later and "
                          "History use the current add-on profile’s native menu."),
                    _disconnected(player, "**Pi connection unavailable.** Home Assistant cannot currently "
                                  "reach Kodi. Waking the LG does not restart the Pi. "
                                  "This connection status does not check Google sign-in."),
                    _note("**If authorization fails:** open YouTube’s main menu and use its native "
                          "Sign in flow. The Google permission screens must be completed in your browser. "
                          "Connection availability alone does not mean the account is authorized."),
                ]},
                {"type": "grid", "cards": [
                    _heading("Pi playback & navigation", "raspberry-pi"),
                    _ready({"type": "media-control", "entity": player, "name": "Current Pi playback"}, player),
                    _ready(_grid(transport), player),
                    _ready(_grid([
                        kodi("Skip back", "replay", "Input.ExecuteAction", action="stepback"),
                        kodi("Skip forward", "fast-forward", "Input.ExecuteAction", action="stepforward"),
                        kodi("Now playing", "television-play", "GUI.ActivateWindow", window="fullscreenvideo"),
                    ]), player),
                    _ready(_grid(navigation), player),
                    _note("The Pi controls affect whichever app is playing in Kodi, including Plex. "
                          "**Kodi home** opens Kodi’s main screen. **Open YouTube** returns to YouTube’s "
                          "menu. Skip buttons follow Kodi’s configured step sizes."),
                ]},
                {"type": "grid", "cards": [
                    _heading("LG TV & soundbar", "television-speaker"),
                    _ready(_button("Show Pi · HDMI 1", "video-input-hdmi", "webostv.command", tv,
                                   command="tv/switchInput", payload={"inputId": "HDMI_1"}), tv),
                    _ready({"type": "tile", "entity": tv, "name": "TV / soundbar volume",
                            "features": [{"type": "media-player-volume-slider"}]}, tv),
                    _ready(_grid([
                        _button("Volume down", "volume-minus", "media_player.volume_down", tv),
                        _button("Volume up", "volume-plus", "media_player.volume_up", tv),
                    ], 2), tv),
                    _button("Wake TV", "power", "media_player.turn_on", tv),
                    _disconnected(tv, "**TV controls unavailable.** Wake TV remains available. "
                                  "Pi playback can continue independently of the LG connection."),
                    _nav("Full LG remote", "remote-tv", "/edsys-pi/lg-remote"),
                    _nav("Plex & Pi player", "plex", "/edsys-pi/player"),
                    _heading("YouTube quality", "high-definition"),
                    _ready(kodi("Stream details", "information-outline", "Input.ShowPlayerProcessInfo"), player),
                    _note("The qualified YouTube preset allows **up to 4K · SDR · 30 fps**, with automatic "
                          "quality selection. The upload, connection and add-on determine the actual "
                          "stream. **Stream details** shows Kodi’s current player information on the TV; "
                          "Back closes that overlay. This page does not "
                          "measure resolution or verify Premium benefits.\n\n"
                          "The LG’s native YouTube app and this Pi add-on have separate sign-ins. "
                          "The full LG remote controls TV menus; this page’s arrows control Kodi."),
                ]},
            ]}


def _player_cards(dashboard):
    if not isinstance(dashboard, dict) or not isinstance(dashboard.get("views"), list):
        raise ValueError("Expected the latest native Bedroom Pi dashboard")
    views = [view for view in dashboard["views"] if isinstance(view, dict) and view.get("path") == "player"]
    if len(views) != 1:
        raise ValueError("Expected exactly one existing Player view")
    sections = views[0].get("sections")
    if (not isinstance(sections, list) or not sections or not isinstance(sections[0], dict)
            or not isinstance(sections[0].get("cards"), list)):
        raise ValueError("Expected the existing Player first section")
    return sections[0]["cards"]


def extend(original, player, tv):
    """Add only one view and one Player link; refuse ownership conflicts.

    Return a deep-copied candidate and counts. The caller must preserve private
    before/candidate files, compare the latest live config immediately before
    saving and require exact native readback. Never rebuild old dashboard views.
    """
    desired = build_view(player, tv)
    result = deepcopy(original)
    cards = _player_cards(result)
    views = [view for view in result["views"] if isinstance(view, dict) and view.get("path") == PATH]
    if len(views) > 1 or (views and views[0] != desired):
        raise ValueError("Existing YouTube view differs; preserve it and review before changing")
    links = [card for card in cards if isinstance(card, dict)
             and card.get("tap_action", {}).get("navigation_path") == URL]
    if len(links) > 1 or (links and links[0] != link()):
        raise ValueError("Existing YouTube link differs; preserve it and review before changing")
    counts = {"views_added": 0, "links_added": 0}
    if not views:
        result["views"].insert(1, desired)
        counts["views_added"] = 1
    if not links:
        cards.append(link())
        counts["links_added"] = 1
    return result, counts


def remove(original, player, tv):
    """Remove only exact owned additions; retain later unrelated dashboard edits."""
    desired = build_view(player, tv)
    result = deepcopy(original)
    cards = _player_cards(result)
    views = [view for view in result["views"] if isinstance(view, dict) and view.get("path") == PATH]
    links = [card for card in cards if isinstance(card, dict)
             and card.get("tap_action", {}).get("navigation_path") == URL]
    if len(views) != 1 or views[0] != desired or len(links) != 1 or links[0] != link():
        raise ValueError("YouTube additions changed or are missing; inspect before recovery")
    result["views"].remove(views[0])
    cards.remove(links[0])
    return result
