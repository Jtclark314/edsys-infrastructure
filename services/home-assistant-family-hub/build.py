"""Build a responsive Family Hub over explicit native HA calendar/to-do bindings.

No event/list contents, credentials, account identifiers or live HA calls belong
in this module. Pass private bindings at deployment time.
"""
from __future__ import annotations

from copy import deepcopy
import re

HUB_PATH = 'edsys-family'
CALENDAR_PRO_TYPE = 'custom:calendar-card-pro'
HOME_AGENDA_HEADING = 'The next seven days'
CALENDAR_AGENDA_HEADING = 'Upcoming family dates'


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
    return upgrade_calendar_agendas({'views': views}, calendar)


def _calendar_agenda(calendar, *, compact):
    """Reviewed Calendar Card Pro 4.2 settings; no event writes or cloud calls."""
    card = {
        'type': CALENDAR_PRO_TYPE, 'entities': [calendar], 'view': 'list',
        'title': 'This week · tap to expand/collapse' if compact else 'The next two weeks',
        'days_to_show': 7 if compact else 14,
        'show_empty_days': False, 'hide_when_empty': False,
        'show_past_events': False, 'show_month': True,
        'show_time': True, 'show_end_time': True,
        'show_location': True, 'show_description': False,
        'show_countdown': False, 'today_indicator': True,
        'title_font_size': '18px', 'event_font_size': '16px',
        'time_font_size': '14px', 'location_font_size': '14px',
        'day_font_size': '28px', 'weekday_font_size': '14px',
        'event_spacing': '8px', 'day_spacing': '16px',
        'title_max_lines': 0, 'location_max_lines': 0,
        'height': 'auto', 'max_height': 'none',
        'refresh_interval': 1, 'refresh_on_navigate': True,
        'tap_action': {'action': 'expand' if compact else 'none'},
        'hold_action': {'action': 'none'},
    }
    if compact:
        card['compact_events_to_show'] = 6
    return card


def _one_view(config, path):
    views = config.get('views')
    if not isinstance(views, list):
        raise ValueError('Expected dashboard views')
    matches = [v for v in views if isinstance(v, dict) and v.get('path') == path]
    if len(matches) != 1 or matches[0].get('type') != 'sections':
        raise ValueError('Expected exactly one owned Sections view')
    if not isinstance(matches[0].get('sections'), list):
        raise ValueError('Expected owned view sections')
    return matches[0]


def _heading_sections(view, heading):
    matches = []
    for section in view['sections']:
        if not isinstance(section, dict) or not isinstance(section.get('cards'), list):
            continue
        headings = [c for c in section['cards'] if isinstance(c, dict)
                    and c.get('type') == 'heading' and c.get('heading') == heading]
        if len(headings) > 1:
            raise ValueError('Ambiguous owned calendar heading')
        if headings:
            matches.append(section)
    if len(matches) > 1:
        raise ValueError('Ambiguous owned calendar section')
    return matches


def _one_calendar(section, calendar, *, kinds):
    cards = [c for c in section['cards'] if isinstance(c, dict) and c.get('type') in kinds]
    if len(cards) != 1 or cards[0].get('entities') != [calendar]:
        raise ValueError('Expected one calendar card with the explicit local binding')
    return cards[0]


def _has_navigation(section, path):
    for card in section['cards']:
        action = card.get('tap_action') if isinstance(card, dict) else None
        if (isinstance(action, dict) and action.get('action') == 'navigate'
                and action.get('navigation_path') == path):
            return True
    return False


def upgrade_calendar_agendas(config, calendar):
    """Patch the owned existing Family Hub agenda, preserving everything else.

    Pass the latest native dashboard and its private local calendar ID. The
    native month card, quick-add controls, shopping bindings, view options and
    unrelated cards are retained verbatim. An already upgraded dashboard is
    returned unchanged, including later agenda appearance edits. Unfamiliar,
    ambiguous or partially upgraded layouts require review instead of a rebuild.
    This pure transformation performs no HA, calendar or list operations.
    """
    calendar = _entity(calendar, 'calendar')
    if not isinstance(config, dict):
        raise ValueError('Expected dashboard configuration')
    candidate = deepcopy(config)
    home = _one_view(candidate, 'home')
    calendar_view = _one_view(candidate, 'calendar')
    home_sections = _heading_sections(home, HOME_AGENDA_HEADING)
    native_sections = _heading_sections(calendar_view, 'Family calendar')
    agenda_sections = _heading_sections(calendar_view, CALENDAR_AGENDA_HEADING)
    if len(home_sections) != 1 or len(native_sections) != 1:
        raise ValueError('Expected the owned Home agenda and native Calendar section')
    home_section, native_section = home_sections[0], native_sections[0]
    home_card = _one_calendar(home_section, calendar,
                              kinds={'calendar', CALENDAR_PRO_TYPE})
    _one_calendar(native_section, calendar, kinds={'calendar'})
    full_path = f'/{HUB_PATH}/calendar'

    if home_card['type'] == CALENDAR_PRO_TYPE:
        if len(agenda_sections) != 1:
            raise ValueError('Partially upgraded calendar layout needs review')
        _one_calendar(agenda_sections[0], calendar, kinds={'calendar', CALENDAR_PRO_TYPE})
        if (not _has_navigation(home_section, full_path)
                or not _has_navigation(agenda_sections[0], '/calendar')
                or not any(c.get('type') == CALENDAR_PRO_TYPE
                           for c in agenda_sections[0]['cards'] if isinstance(c, dict))):
            raise ValueError('Partially upgraded calendar controls need review')
        return candidate

    if agenda_sections:
        raise ValueError('Partially upgraded calendar layout needs review')
    expected = {'type': 'calendar', 'entities': [calendar],
                'initial_view': 'listWeek', 'show_add_event': False}
    # These HA layout fields are valid on either card. Other native-card
    # customizations may have incompatible semantics and need explicit review.
    layout = {key: deepcopy(home_card[key]) for key in ('grid_options', 'visibility')
              if key in home_card}
    if {k: v for k, v in home_card.items() if k not in layout} != expected:
        raise ValueError('Customized native Home calendar needs review')
    agenda = {**_calendar_agenda(calendar, compact=True), **layout}
    home_section['cards'][home_section['cards'].index(home_card)] = agenda
    if not _has_navigation(home_section, full_path):
        home_section['cards'].append(shortcut('More dates & calendar', 'mdi:calendar-month', full_path))

    rich_section = _section(CALENDAR_AGENDA_HEADING, 'mdi:calendar-clock', [
        _calendar_agenda(calendar, compact=False),
        {'type': 'markdown', 'content': 'Agenda times follow this device’s time zone. The add form uses home time.'},
        shortcut('Open calendar editor', 'mdi:calendar-edit', '/calendar'),
    ])
    calendar_view['sections'].insert(calendar_view['sections'].index(native_section), rich_section)
    return candidate


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
