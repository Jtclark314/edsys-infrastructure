import copy
import unittest

from build import add_navigation, build_dashboard, upgrade_calendar_agendas


def legacy_hub():
    """Synthetic prior dashboard with user fields and private-binding examples."""
    native = {'type': 'calendar', 'entities': ['calendar.family_example'],
              'show_add_event': False}
    return {'title': 'Household example', 'background': 'var(--card-background-color)',
            'views': [
                {'path': 'home', 'type': 'sections', 'max_columns': 2,
                 'badges': [{'type': 'entity', 'entity': 'sensor.example'}],
                 'sections': [
                     {'type': 'grid', 'cards': [
                         {'type': 'heading', 'heading': 'The next seven days'},
                         {**native, 'initial_view': 'listWeek',
                          'grid_options': {'columns': 'full'},
                          'visibility': [{'condition': 'user', 'users': ['example-user']}]},
                         {'type': 'markdown', 'content': 'Owner-added note'},
                     ]},
                     {'type': 'grid', 'cards': [
                         {'type': 'custom:edsys-family-event-card',
                          'entity': 'calendar.family_example', 'allow_recurrence': True},
                         {'type': 'custom:edsys-family-shopping-card',
                          'entities': ['todo.owner_selected_example'], 'title': 'Selected list'},
                     ]},
                 ]},
                {'path': 'calendar', 'type': 'sections', 'max_columns': 2,
                 'sections': [
                     {'type': 'grid', 'column_span': 1, 'cards': [
                         {'type': 'heading', 'heading': 'Family calendar'},
                         {**native, 'initial_view': 'dayGridMonth',
                          'grid_options': {'columns': 'full', 'rows': 8}},
                     ]},
                     {'type': 'grid', 'cards': [
                         {'type': 'custom:edsys-family-event-card',
                          'entity': 'calendar.family_example', 'allow_recurrence': True},
                     ]},
                 ]},
                {'path': 'shopping', 'type': 'sections', 'sections': [
                    {'type': 'grid', 'cards': [
                        {'type': 'custom:edsys-family-shopping-card',
                         'entities': ['todo.owner_selected_example'], 'title': 'Selected list'},
                    ]},
                ]},
                {'path': 'owner-extra', 'cards': [{'type': 'markdown', 'content': 'Keep this view'}]},
            ]}


class FamilyDashboardTests(unittest.TestCase):
    def test_fixed_calendar_and_selected_lists_only(self):
        bindings = {'calendar': 'calendar.family_example', 'shopping_entities': ['todo.groceries_example']}
        original = copy.deepcopy(bindings)
        config = build_dashboard(bindings)
        self.assertEqual(bindings, original)
        self.assertEqual([v['path'] for v in config['views']], ['home', 'calendar', 'shopping', 'help'])
        cards = [c for v in config['views'] for s in v['sections'] for c in s['cards']]
        for card in cards:
            if card['type'] == 'calendar':
                self.assertEqual(card['entities'], ['calendar.family_example'])
                self.assertIs(card['show_add_event'], False)
            elif card['type'] == 'custom:calendar-card-pro':
                self.assertEqual(card['entities'], ['calendar.family_example'])
            elif card['type'] == 'custom:edsys-family-event-card':
                self.assertEqual(card['entity'], 'calendar.family_example')
            elif card['type'] == 'custom:edsys-family-shopping-card':
                self.assertEqual(card['entities'], ['todo.groceries_example'])

    def test_pending_lists_are_not_bound_to_an_unrelated_list(self):
        config = build_dashboard({'calendar': 'calendar.family_example'})
        cards = [c for v in config['views'] for s in v['sections'] for c in s['cards']]
        self.assertTrue(all(c['entities'] == [] for c in cards if c['type'] == 'custom:edsys-family-shopping-card'))

    def test_existing_views_and_unrelated_controls_preserved(self):
        original = {'title': 'Existing', 'views': [
            {'path': 'plans', 'sections': [{'type': 'grid', 'cards': [{'type': 'calendar', 'entities': ['calendar.existing']}]}]},
            {'path': 'other', 'sections': [{'type': 'grid', 'cards': [{'type': 'tile', 'entity': 'light.example'}]}]},
        ]}
        baseline = copy.deepcopy(original)
        changed = add_navigation(original, 'plans')
        self.assertEqual(original, baseline)
        self.assertEqual(changed['views'][1], baseline['views'][1])
        self.assertEqual(changed['views'][0]['sections'][0]['cards'][:-1], baseline['views'][0]['sections'][0]['cards'])
        self.assertEqual(add_navigation(changed, 'plans'), changed)
        link = changed['views'][0]['sections'][0]['cards'][-1]
        self.assertEqual(link['tap_action'], {'action': 'navigate', 'navigation_path': '/edsys-family/home'})

    def test_invalid_or_ambiguous_targets_refused(self):
        for bad in ['all', 'calendar.*', 'todo.example', '../calendar.family']:
            with self.assertRaises(ValueError):
                build_dashboard({'calendar': bad})
        with self.assertRaises(ValueError):
            build_dashboard({'calendar': 'calendar.family_example', 'shopping_entities': ['todo.example', 'todo.example']})
        with self.assertRaises(ValueError):
            add_navigation({'views': [{'path': 'plans'}, {'path': 'plans'}]}, 'plans')

    def test_agenda_upgrade_preserves_native_editor_quickadd_shopping_and_user_fields(self):
        original = legacy_hub()
        baseline = copy.deepcopy(original)
        changed = upgrade_calendar_agendas(original, 'calendar.family_example')
        self.assertEqual(original, baseline)
        self.assertEqual({k: v for k, v in changed.items() if k != 'views'},
                         {k: v for k, v in baseline.items() if k != 'views'})
        home, calendar, *other = changed['views']
        self.assertEqual(other, baseline['views'][2:])
        self.assertEqual({k: v for k, v in home.items() if k != 'sections'},
                         {k: v for k, v in baseline['views'][0].items() if k != 'sections'})
        self.assertEqual(home['sections'][1:], baseline['views'][0]['sections'][1:])
        self.assertEqual(home['sections'][0]['cards'][0], baseline['views'][0]['sections'][0]['cards'][0])
        self.assertEqual(home['sections'][0]['cards'][2], baseline['views'][0]['sections'][0]['cards'][2])
        self.assertEqual(calendar['sections'][1:], baseline['views'][1]['sections'])
        self.assertEqual({k: v for k, v in calendar.items() if k != 'sections'},
                         {k: v for k, v in baseline['views'][1].items() if k != 'sections'})
        agenda = home['sections'][0]['cards'][1]
        prior = baseline['views'][0]['sections'][0]['cards'][1]
        self.assertEqual(agenda['grid_options'], prior['grid_options'])
        self.assertEqual(agenda['visibility'], prior['visibility'])

    def test_agendas_are_local_read_only_readable_and_have_explicit_editor_navigation(self):
        changed = upgrade_calendar_agendas(legacy_hub(), 'calendar.family_example')
        home_cards = changed['views'][0]['sections'][0]['cards']
        full_cards = changed['views'][1]['sections'][0]['cards']
        home = next(c for c in home_cards if c['type'] == 'custom:calendar-card-pro')
        full = next(c for c in full_cards if c['type'] == 'custom:calendar-card-pro')
        self.assertEqual(home['compact_events_to_show'], 6)
        self.assertEqual(home['days_to_show'], 7)
        self.assertEqual(home['tap_action'], {'action': 'expand'})
        self.assertIn('tap to expand', home['title'])
        self.assertEqual(full['days_to_show'], 14)
        self.assertNotIn('compact_events_to_show', full)
        self.assertTrue(any(c.get('tap_action') == {
            'action': 'navigate', 'navigation_path': '/calendar'} for c in full_cards))
        self.assertTrue(any(c.get('type') == 'markdown' and 'device’s time zone' in c.get('content', '')
                            and 'home time' in c.get('content', '') for c in full_cards))
        self.assertTrue(any(c.get('tap_action') == {
            'action': 'navigate', 'navigation_path': '/edsys-family/calendar'} for c in home_cards))
        for agenda in (home, full):
            self.assertEqual(agenda['entities'], ['calendar.family_example'])
            self.assertEqual(agenda['view'], 'list')
            self.assertGreaterEqual(int(agenda['event_font_size'].removesuffix('px')), 16)
            self.assertGreaterEqual(int(agenda['time_font_size'].removesuffix('px')), 14)
            self.assertEqual(agenda['height'], 'auto')
            self.assertEqual(agenda['max_height'], 'none')
            self.assertIs(agenda['hide_when_empty'], False)
            self.assertIs(agenda['show_description'], False)
            self.assertEqual(agenda['refresh_interval'], 1)
            self.assertTrue(agenda['refresh_on_navigate'])
            self.assertFalse({'weather', 'service', 'url', 'cloud', 'api_key'} & agenda.keys())

    def test_upgraded_agendas_are_idempotent_and_preserve_later_presentation_edits(self):
        upgraded = upgrade_calendar_agendas(legacy_hub(), 'calendar.family_example')
        upgraded['views'][0]['sections'][0]['cards'][1]['event_font_size'] = '20px'
        upgraded['views'][1]['sections'][0]['cards'][1]['title'] = 'Owner-customized agenda'
        self.assertEqual(upgrade_calendar_agendas(upgraded, 'calendar.family_example'), upgraded)

    def test_ambiguous_foreign_customized_and_partial_calendar_layouts_refused(self):
        variants = []
        duplicate_view = legacy_hub()
        duplicate_view['views'].append(copy.deepcopy(duplicate_view['views'][0]))
        variants.append(duplicate_view)
        duplicate_section = legacy_hub()
        duplicate_section['views'][0]['sections'].append(copy.deepcopy(duplicate_section['views'][0]['sections'][0]))
        variants.append(duplicate_section)
        foreign_home = legacy_hub()
        foreign_home['views'][0]['sections'][0]['cards'][1]['entities'] = ['calendar.other_example']
        variants.append(foreign_home)
        foreign_month = legacy_hub()
        foreign_month['views'][1]['sections'][0]['cards'][1]['entities'] = ['calendar.other_example']
        variants.append(foreign_month)
        customized_home = legacy_hub()
        customized_home['views'][0]['sections'][0]['cards'][1]['initial_view'] = 'dayGridMonth'
        variants.append(customized_home)
        partial = upgrade_calendar_agendas(legacy_hub(), 'calendar.family_example')
        del partial['views'][1]['sections'][0]
        variants.append(partial)
        for config in variants:
            with self.subTest(config=config):
                before = copy.deepcopy(config)
                with self.assertRaises(ValueError):
                    upgrade_calendar_agendas(config, 'calendar.family_example')
                self.assertEqual(config, before)


if __name__ == '__main__':
    unittest.main()
