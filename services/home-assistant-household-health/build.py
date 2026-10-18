"""Build a read-only Household Health dashboard from private HA bindings.

This module performs no network, service, file, registry or entity operations.
Bindings are configuration, never an assertion that a source is currently live.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re


DASHBOARD_PATH = "edsys-health"
DASHBOARD_METADATA = {
    "url_path": DASHBOARD_PATH,
    "title": "Household Health",
    "icon": "mdi:home-heart",
    "show_in_sidebar": True,
    "require_admin": True,
    "mode": "storage",
}
_REASONS = {"disabled", "retired", "intentionally_offline"}
_ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]+")
_REPAIR_NAMES = {"active": "Active issues", "ignored": "Ignored issues", "total": "All issues"}
# Unknown is often the normal never-used/no-trigger state for these domains.
# Only the generic unknown warning uses this bounded semantic exception;
# unavailable states and explicit watched-source checks remain unfiltered.
_UNKNOWN_NON_REPORTING_DOMAINS = (
    "button", "scene", "event", "notify", "stt", "tts", "conversation", "ai_task", "image",
)


def _entity(value, field):
    if not isinstance(value, str) or not _ENTITY.fullmatch(value):
        raise ValueError(f"{field} requires an explicit entity ID")
    return value


def _entities(value, field, *, maximum=128, domains=None):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} requires a bounded list of explicit entity IDs")
    result = [_entity(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains a duplicate entity")
    if domains and any(item.split(".", 1)[0] not in domains for item in result):
        raise ValueError(f"{field} contains an unsupported domain")
    return result


def _bindings(bindings):
    if not isinstance(bindings, dict):
        raise ValueError("bindings must be an object")
    allowed = {"repair_sensors", "excluded_entities", "watched_entities", "diagnostic_entities",
               "battery_threshold", "auto_entities_available"}
    if set(bindings) - allowed:
        raise ValueError("Unrecognized Household Health binding")
    threshold = bindings.get("battery_threshold", 20)
    if type(threshold) is not int or not 1 <= threshold <= 50:
        raise ValueError("battery_threshold must be an integer from 1 to 50")
    auto = bindings.get("auto_entities_available", True)
    if type(auto) is not bool:
        raise ValueError("auto_entities_available must be a boolean")
    excluded = bindings.get("excluded_entities", [])
    if not isinstance(excluded, list) or len(excluded) > 2500:
        raise ValueError("excluded_entities must be a bounded list")
    exclusions = {}
    for item in excluded:
        if not isinstance(item, dict) or set(item) != {"entity", "reason"}:
            raise ValueError("Each exclusion requires only entity and reason")
        entity = _entity(item["entity"], "excluded_entities")
        if entity in exclusions or not isinstance(item["reason"], str) or item["reason"] not in _REASONS:
            raise ValueError("Duplicate exclusion or unsupported exclusion reason")
        exclusions[entity] = item["reason"]
    watched = _entities(bindings.get("watched_entities", []), "watched_entities")
    diagnostics = _entities(bindings.get("diagnostic_entities", []), "diagnostic_entities",
                            domains={"sensor", "binary_sensor"})
    repair = bindings.get("repair_sensors", {})
    if not isinstance(repair, dict) or set(repair) - set(_REPAIR_NAMES):
        raise ValueError("repair_sensors supports active, ignored and total only")
    repair = {key: _entity(value, "repair_sensors") for key, value in repair.items()}
    if any(not value.startswith("sensor.") for value in repair.values()):
        raise ValueError("repair_sensors requires sensor entity IDs")
    if len(set(repair.values())) != len(repair):
        raise ValueError("Repair sensor bindings must be distinct")
    if set(exclusions) & (set(watched) | set(diagnostics) | set(repair.values())):
        raise ValueError("A watched or repair source cannot also be excluded")
    return {"threshold": threshold, "auto": auto, "excluded": sorted(exclusions),
            "exclusions": exclusions, "watched": watched, "diagnostics": diagnostics,
            "repairs": repair}


def _markdown(title, content):
    return {"type": "markdown", "title": title, "content": content}


def _section(title, icon, cards):
    return {"type": "grid", "cards": [
        {"type": "heading", "heading": title, "heading_style": "title", "icon": icon},
        *cards,
    ]}


def _shortcut(name, icon, path):
    return {"type": "button", "name": name, "icon": icon, "show_state": False,
            "tap_action": {"action": "navigate", "navigation_path": path},
            "hold_action": {"action": "none"}, "double_tap_action": {"action": "none"}}


def _selection(kind, bindings):
    """Common server-side selection for custom lists and their native fallback."""
    threshold = bindings["threshold"]
    predicate = {
        "unavailable": "e.state == 'unavailable'",
        "unknown": ("e.state == 'unknown' and e.domain not in "
                    + json.dumps(_UNKNOWN_NON_REPORTING_DOMAINS)),
        "low_battery": (
            "(e.domain == 'sensor' and e.attributes.get('device_class') == 'battery' "
            "and e.attributes.get('unit_of_measurement') == '%' and is_number(e.state) "
            f"and 0 <= e.state | float <= {threshold}) or "
            "(e.domain == 'binary_sensor' and e.attributes.get('device_class') == 'battery' "
            "and e.state == 'on')"
        ),
        "battery_readings": (
            "(e.domain == 'sensor' and e.attributes.get('device_class') == 'battery' and "
            "(e.attributes.get('unit_of_measurement') != '%' or not is_number(e.state) "
            "or not (0 <= e.state | float(0) <= 100))) or "
            "(e.domain == 'binary_sensor' and e.attributes.get('device_class') == 'battery' "
            "and e.state not in ['on', 'off'])"
        ),
        "updates": "e.domain == 'update' and e.state == 'on'",
        "problems": (
            "e.domain == 'binary_sensor' and "
            "((e.attributes.get('device_class') == 'problem' and e.state == 'on') or "
            "(e.attributes.get('device_class') == 'connectivity' and e.state == 'off'))"
        ),
        "diagnostics": f"e.entity_id in {json.dumps(bindings['diagnostics'])}",
    }[kind]
    return (
        "{% set excluded = " + json.dumps(bindings["excluded"]) + " %}\n"
        "{% set ns = namespace(items=[]) %}\n"
        "{% for e in states if e.entity_id not in excluded %}\n"
        "{% if " + predicate + " %}{% set ns.items = ns.items + [e] %}{% endif %}\n"
        "{% endfor %}\n"
    )


def _list(kind, title, empty, bindings):
    selection = _selection(kind, bindings)
    if not bindings["auto"]:
        return _markdown(title, selection + """{% if ns.items | count == 0 %}
__EMPTY__
{% else %}
{% for e in ns.items | sort(attribute='name') %}
- **{{ e.name | e }}**: {{ e.state | e }}{% if e.attributes.get('unit_of_measurement') %} {{ e.attributes.unit_of_measurement | e }}{% endif %}
{% endfor %}
{% endif %}""".replace("__EMPTY__", empty))
    template = selection + """{% set output = namespace(rows=[]) %}
{% for e in ns.items %}
{% set output.rows = output.rows + [{
  'entity': e.entity_id, 'type': 'simple-entity', 'secondary_info': 'last_updated',
  'tap_action': {'action': 'more-info'}, 'hold_action': {'action': 'none'},
  'double_tap_action': {'action': 'none'}
}] %}
{% endfor %}
{{ output.rows | to_json }}"""
    return {
        "type": "custom:auto-entities", "unique": True,
        "card": {"type": "entities", "title": title, "show_header_toggle": False},
        "filter": {"template": template},
        "sort": {"method": "name", "ignore_case": True},
        "else": _markdown(title, empty),
    }


def _overview(bindings):
    content = """{% set excluded = __EXCLUDED__ %}
{% set normal_unknown_domains = __NORMAL_UNKNOWN_DOMAINS__ %}
{% set ns = namespace(unavailable=0, unknown=0, reporting=0, skipped_unknown=0) %}
{% for e in states if e.entity_id not in excluded %}
{% if e.state == 'unavailable' %}{% set ns.unavailable = ns.unavailable + 1 %}
{% elif e.state == 'unknown' and e.domain in normal_unknown_domains %}{% set ns.skipped_unknown = ns.skipped_unknown + 1 %}
{% elif e.state == 'unknown' %}{% set ns.unknown = ns.unknown + 1 %}
{% else %}{% set ns.reporting = ns.reporting + 1 %}{% endif %}
{% endfor %}
**{{ ns.unavailable }} unavailable** · **{{ ns.unknown }} not reporting**

{{ ns.reporting }} other entity states are present. {{ ns.skipped_unknown }} unknown action, trigger, voice-service or image statuses are omitted from these warnings. These counts cover Home Assistant's current states, not a test of each physical device.

View time: {{ now().strftime('%b %d, %I:%M %p %Z') }}.
If a list does not load, refresh the page or open Devices & services below.
""".replace("__EXCLUDED__", json.dumps(bindings["excluded"])).replace(
        "__NORMAL_UNKNOWN_DOMAINS__", json.dumps(_UNKNOWN_NON_REPORTING_DOMAINS))
    return _markdown("Current reports", content)


def _repairs(bindings):
    if not bindings["repairs"]:
        return _markdown("Repair reports", "Repair counts are not configured. Open **Repairs** for current issues; a missing count is not zero issues.")
    content = []
    for key in _REPAIR_NAMES:
        label = _REPAIR_NAMES[key]
        entity = bindings["repairs"].get(key)
        if entity is None:
            content.append(f"**{label}:** not configured.\n")
            continue
        content.append("""{% set source = states['__ENTITY__'] %}
**__LABEL__:** {% if source is none %}not present.
{% elif source.state == 'unavailable' %}unavailable.
{% elif source.state == 'unknown' %}not reporting.
{% elif not is_number(source.state) or source.state | float < 0 or source.state | float != source.state | int %}invalid count.
{% else %}{{ source.state | int }}.
{% endif %}
{% if source is not none %}Count last updated: {{ as_timestamp(source.last_updated) | timestamp_custom('%b %d, %I:%M %p %Z', true) }}.
{% endif %}
""".replace("__ENTITY__", entity).replace("__LABEL__", label))
    content.append("Counts are reported by Repairs. Ignored issues remain issues; zero reported issues does not establish whole-house health.")
    return _markdown("Repair reports", "\n".join(content))


def _sources(bindings):
    selected = list(dict.fromkeys(bindings["watched"] + bindings["diagnostics"]))
    if not selected:
        return _markdown("Selected sources", "No individual sources have been selected for missing-source checks. The lists above still show current entity reports.")
    content = """{% set selected = __SELECTED__ %}
{% for entity in selected %}
{% set source = states[entity] %}
- {% if source is none %}**{{ entity | e }} — missing:** no current state.
{% elif source.state == 'unavailable' %}**{{ source.name | e }} — unavailable.**
{% elif source.state == 'unknown' %}**{{ source.name | e }} — not reporting.**
{% else %}**{{ source.name | e }}:** {{ source.state | e }}.
{% endif %}
{% endfor %}

Missing sources need a registry or integration check. A device's off, idle or standby state alone is not a connection failure.
""".replace("__SELECTED__", json.dumps(selected))
    return _markdown("Selected sources", content)


def _coverage(bindings):
    reasons = bindings["exclusions"].values()
    content = (
        f"Selected exclusions: **{list(reasons).count('disabled')} disabled**, "
        f"**{list(reasons).count('retired')} retired**, "
        f"**{list(reasons).count('intentionally_offline')} intentionally offline**.\n\n"
        "These selections are omitted from the attention lists. Review them when a device is returned to service. "
        "Entities disabled in Home Assistant usually have no current state and cannot be discovered by these lists.\n\n"
        "Unknown action, trigger, voice-service and image statuses are omitted from not-reporting warnings: "
        "they may be unused or waiting for a trigger or image. Their unavailable states and explicit "
        "selected-source checks remain visible.\n\n"
        "Battery percentages must be numeric, between 0 and 100, and reported in %. "
        "Voltage-only and missing readings are shown separately. Last updated is the time Home Assistant's state last changed or was updated, not proof of a recent physical test."
    )
    return _markdown("What this page covers", content)


def build_dashboard(bindings):
    """Return new config only. Registration must retain admin-only access."""
    bound = _bindings(bindings)
    links = {"type": "grid", "columns": 3, "square": False, "cards": [
        _shortcut("Repairs", "mdi:wrench-outline", "/config/repairs"),
        _shortcut("Devices & services", "mdi:devices", "/config/integrations/dashboard"),
        _shortcut("Updates", "mdi:update", "/config/updates"),
    ]}
    sections = [
        _section("Household Health", "mdi:home-heart", [_overview(bound), _repairs(bound), links]),
        _section("Availability", "mdi:lan-disconnect", [
            _list("unavailable", "Unavailable now", "No current non-excluded entities report unavailable. Missing sources are checked separately below.", bound),
            _list("unknown", "Not reporting", "No current non-excluded reporting sources have an unknown state. Normal unused action, trigger, voice-service and image statuses are omitted; disabled or missing entities are checked separately.", bound),
        ]),
        _section("Battery watch", "mdi:battery-heart-outline", [
            _markdown("Battery warnings", f"Reported battery levels at or below **{bound['threshold']}%**, plus devices that explicitly report a low battery. Missing or invalid readings are kept separate."),
            _list("low_battery", "Low battery reports", "No matching low-battery reports. Check the readings below for missing or unsupported data.", bound),
            _list("battery_readings", "Battery readings to check", "No current battery entities have missing, invalid or unsupported percentage readings. This does not discover batteries without an entity.", bound),
        ]),
        _section("Updates & connections", "mdi:clipboard-pulse-outline", [
            _list("updates", "Updates reported available", "No non-excluded update entities currently report an available update. This is not a check of every installed component.", bound),
            _list("problems", "Connection & problem reports", "No matching connection or problem warnings are currently reported.", bound),
            _list("diagnostics", "Selected checks", "No selected diagnostic sources currently have a state. Check Selected sources below for missing entries.", bound),
        ]),
        _section("Selected sources", "mdi:playlist-check", [_sources(bound)]),
        _section("Coverage", "mdi:information-outline", [_coverage(bound)]),
    ]
    return {"title": "Household Health", "views": [
        {"title": "Household Health", "path": "home", "icon": "mdi:home-heart",
         "type": "sections", "max_columns": 3, "sections": sections},
    ]}


def _view(config, path):
    if not isinstance(config, dict) or not isinstance(config.get("views"), list):
        raise ValueError("Expected a dashboard with views")
    matches = [value for value in config["views"] if isinstance(value, dict) and value.get("path") == path]
    if len(matches) != 1 or matches[0].get("type") != "sections":
        raise ValueError("Expected exactly one existing Sections view")
    view = matches[0]
    if not isinstance(view.get("sections"), list) or not view["sections"]:
        raise ValueError("Expected existing sections")
    for section in view["sections"]:
        if not isinstance(section, dict) or section.get("type") != "grid" or not isinstance(section.get("cards"), list):
            raise ValueError("Expected grid sections with card lists")
    return view


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def add_navigation(config, view_path="plans"):
    """Append only our link to an explicitly selected Household Sections view."""
    candidate = deepcopy(config)
    view = _view(candidate, view_path)
    link = _shortcut("Household Health", "mdi:home-heart", f"/{DASHBOARD_PATH}/home")
    existing = [card for card in _walk(view) if card.get("name") == link["name"] or
                card.get("tap_action", {}).get("navigation_path") == f"/{DASHBOARD_PATH}/home"]
    if existing:
        if len(existing) == 1 and existing[0] == link:
            return candidate
        raise ValueError("Existing Household Health navigation differs or is ambiguous")
    view["sections"][0]["cards"].append(link)
    return candidate


def _section_key(section):
    headings = [card.get("heading") for card in section.get("cards", [])
                if isinstance(card, dict) and card.get("type") == "heading"]
    if not headings or not isinstance(headings[0], str):
        raise ValueError("Owned section needs an explicit heading")
    return headings[0]


def reconcile_dashboard(current, previous, desired):
    """Apply only changed owned sections against private prior config.

    Unchanged/later sections and view metadata are preserved. A concurrently
    changed owned section is a conflict, never an excuse to overwrite it.
    Identity/layout migrations and section addition/removal require review.
    This also supports narrow recovery by swapping previous and desired.
    """
    candidate = deepcopy(current)
    actual_view = _view(candidate, "home")
    before_view = _view(previous, "home")
    after_view = _view(desired, "home")
    if len(previous["views"]) != 1 or len(desired["views"]) != 1:
        raise ValueError("Expected exactly the owned dashboard view")
    if {k: v for k, v in previous.items() if k != "views"} != {k: v for k, v in desired.items() if k != "views"}:
        raise ValueError("Dashboard metadata migration requires review")
    if {k: v for k, v in before_view.items() if k != "sections"} != {k: v for k, v in after_view.items() if k != "sections"}:
        raise ValueError("View metadata migration requires review")
    before = {_section_key(section): section for section in before_view["sections"]}
    after = {_section_key(section): section for section in after_view["sections"]}
    if len(before) != len(before_view["sections"]) or len(after) != len(after_view["sections"]) or before.keys() != after.keys():
        raise ValueError("Owned section identity migration requires review")
    for key, old in before.items():
        new = after[key]
        if old == new:
            continue
        indexes = []
        for index, section in enumerate(actual_view["sections"]):
            try:
                actual_key = _section_key(section)
            except ValueError:
                # A later, unrelated section may legitimately have no heading.
                continue
            if actual_key == key:
                indexes.append(index)
        if len(indexes) != 1:
            raise ValueError("Missing or ambiguous owned section")
        index = indexes[0]
        if actual_view["sections"][index] == new:
            continue
        if actual_view["sections"][index] != old:
            raise ValueError("Owned section changed; review concurrent edits")
        actual_view["sections"][index] = deepcopy(new)
    return candidate
