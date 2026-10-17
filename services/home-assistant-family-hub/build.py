"""Build a responsive Family Hub over explicit native HA calendar/to-do bindings.

No event/list contents, credentials, account identifiers or live HA calls belong
in this module. Pass private bindings at deployment time.
"""
from __future__ import annotations

from copy import deepcopy
import re

HUB_PATH = 'edsys-family'


def _entity(value, domain):
    if not isinstance(value, str) or not re.fullmatch(rf'{domain}\.[a-z0-9_]+', value):
        raise ValueError(f'Expected one explicit {domain} entity')
    return value


def shortcut(name, icon, path):
    return {'type': 'button', 'name': name, 'icon': icon,
            'show_state': False, 'tap_action': {'action': 'navigate', 'navigation_path': path},
            'hold_action': {'action': 'none'}, 'double_tap_action': {'action': 'none'}}


def _section(title, icon, cards, **extra):
    return {'type': 'grid', 'cards': [{'type': 'heading', 'heading': title,
            'heading_style': 'title', 'icon': icon}, *cards], **extra}


def build_dashboard(bindings):
    """Return only the new dashboard config. Existing dashboards stay separate."""
    calendar = _entity(bindings.get('calendar'), 'calendar')
    lists = bindings.get('shopping_entities', [])
    if not isinstance(lists, list) or len(lists) > 12:
        raise ValueError('shopping_entities must contain at most 12 explicit IDs')
    lists = [_entity(value, 'todo') for value in lists]
    if len(set(lists)) != len(lists):
        raise ValueError('Duplicate shopping entity')
    event_card = {'type': 'custom:edsys-family-event-card', 'entity': calendar,
                  'title': 'Add a family date', 'allow_recurrence': bool(bindings.get('allow_recurrence', False))}
    shopping_card = {'type': 'custom:edsys-family-shopping-card', 'entities': lists,
                     'title': 'Family shopping'}
    calendar_card = {'type': 'calendar', 'entities': [calendar],
                     'initial_view': 'listWeek', 'show_add_event': False}
    navigation = {'type': 'grid', 'columns': 2, 'square': False, 'cards': [
        shortcut('Family calendar', 'mdi:calendar-heart', f'/{HUB_PATH}/calendar'),
        shortcut('Shopping lists', 'mdi:cart-outline', f'/{HUB_PATH}/shopping'),
    ]}
    views = [
        {'title': 'Family Hub', 'path': 'home', 'type': 'sections', 'max_columns': 2,
         'icon': 'mdi:home-heart', 'sections': [
             _section('Family Hub', 'mdi:home-heart', [navigation,
                 {'type': 'markdown', 'content': 'Add a date, check the week ahead, or update the family shopping list.'}]),
             _section('The next seven days', 'mdi:calendar-week', [deepcopy(calendar_card)]),
             _section('Add a date', 'mdi:calendar-plus', [deepcopy(event_card)]),
             _section('Shopping', 'mdi:cart-outline', [deepcopy(shopping_card)]),
         ]},
        {'title': 'Calendar', 'path': 'calendar', 'type': 'sections', 'max_columns': 2,
         'icon': 'mdi:calendar-heart', 'sections': [
             _section('Family calendar', 'mdi:calendar-heart', [
                 {**deepcopy(calendar_card), 'initial_view': 'dayGridMonth'}]),
             _section('Add a family date', 'mdi:calendar-plus', [deepcopy(event_card),
                 {'type': 'markdown', 'content': 'Times use the home time zone shown above. For an existing event, open it on the calendar to edit or delete it.'}]),
         ]},
        {'title': 'Shopping', 'path': 'shopping', 'type': 'sections', 'max_columns': 2,
         'icon': 'mdi:cart-outline', 'sections': [
             _section('Family shopping', 'mdi:cart-outline', [deepcopy(shopping_card)]),
             _section('AnyList', 'mdi:format-list-checks', [
                 {'type': 'markdown', 'content': 'Use AnyList on your phone for quantities, item notes, recipes and store details. Changes from another device can take about a minute to appear here; use **Refresh list** to check again.'},
                 {'type': 'button', 'name': 'Open AnyList', 'icon': 'mdi:open-in-new',
                  'tap_action': {'action': 'url', 'url_path': 'https://www.anylist.com/web'},
                  'hold_action': {'action': 'none'}, 'double_tap_action': {'action': 'none'}},
             ]),
         ]},
        {'title': 'Help', 'path': 'help', 'type': 'sections', 'max_columns': 1,
         'icon': 'mdi:help-circle-outline', 'sections': [_section('Use it anywhere', 'mdi:cellphone', [
             {'type': 'markdown', 'content': '### On your phone\nOpen Family Hub in the Home Assistant app, or save this page to your browser’s home screen. Sign in with your own household account. Away from home, use the household’s secure Home Assistant connection.\n\n### On the tablet\nChoose **Family Hub** in the sidebar. The same calendar and shopping lists are used on every device.\n\n### Calendar\nFamily dates are stored in Home Assistant. Adding a date here does not add it to your phone’s separate Calendar app.\n\n### When a save is uncertain\nYour entry stays on screen. Check the calendar or refresh the shopping list before submitting it again.'},
         ])]},
    ]
    return {'views': views}


def add_navigation(config, view_path):
    """Add one navigation-only button; reject ambiguous/missing view matches."""
    candidate = deepcopy(config)
    matches = [v for v in candidate.get('views', []) if v.get('path') == view_path]
    if len(matches) != 1:
        raise ValueError('Expected exactly one existing view')
    view = matches[0]
    link = shortcut('Family Hub', 'mdi:home-heart', f'/{HUB_PATH}/home')
    if any(c.get('name') == 'Family Hub' and c.get('tap_action', {}).get('navigation_path') == f'/{HUB_PATH}/home'
           for section in view.get('sections', []) for c in section.get('cards', [])):
        return candidate
    if not view.get('sections'):
        raise ValueError('Expected an existing Sections view')
    view['sections'][0]['cards'].append(link)
    return candidate
