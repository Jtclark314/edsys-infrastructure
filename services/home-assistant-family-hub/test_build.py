import copy
import unittest

from build import add_navigation, build_dashboard


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


if __name__ == '__main__':
    unittest.main()
