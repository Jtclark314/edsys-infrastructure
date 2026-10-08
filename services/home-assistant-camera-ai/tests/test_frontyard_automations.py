import datetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from jinja2 import Environment, StrictUndefined

spec = importlib.util.spec_from_file_location('frontyard_automations', Path(__file__).parents[1] / 'frontyard-automations.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FrontyardAutomationTests(unittest.TestCase):
    def setUp(self):
        self.configs = module.build('fixture-provider', ['binary_sensor.fixture_person'], ['binary_sensor.fixture_car', 'binary_sensor.fixture_motorcycle'])
        self.env = Environment(undefined=StrictUndefined)

    def test_no_raw_motion_trigger_and_no_startup_unknown_trigger(self):
        for config in self.configs:
            trigger = config['triggers'][0]
            self.assertEqual(trigger['from'], 'off')
            self.assertEqual(trigger['to'], 'on')
            self.assertEqual(trigger['for']['seconds'], 2)
            self.assertNotIn('binary_sensor.frontyard_motion', trigger['entity_id'])
            self.assertEqual(config['mode'], 'single')
            self.assertNotIn('initial_state', config)

    def test_person_and_vehicle_cooldowns_are_independent(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        evaluate = lambda last: self.env.from_string(module.COOLDOWN).render(this=SimpleNamespace(attributes=SimpleNamespace(last_triggered=last)), now=lambda: now)
        self.assertEqual(evaluate(None), 'True')
        self.assertEqual(evaluate(now - datetime.timedelta(seconds=119)), 'False')
        self.assertEqual(evaluate(now - datetime.timedelta(seconds=120)), 'True')
        self.assertNotEqual(self.configs[0]['id'], self.configs[1]['id'])
        self.assertEqual(evaluate(None), 'True')

    def test_one_bounded_analysis_with_no_second_title_call_or_retry(self):
        for config in self.configs:
            analyzer = config['actions'][1]
            self.assertEqual(analyzer['action'], 'llmvision.stream_analyzer')
            self.assertTrue(analyzer['continue_on_error'])
            self.assertEqual(analyzer['data']['duration'], 5)
            self.assertEqual(analyzer['data']['max_frames'], 3)
            self.assertEqual(analyzer['data']['max_tokens'], 500)
            self.assertFalse(analyzer['data']['generate_title'])
            self.assertFalse(analyzer['data']['store_in_timeline'])
            self.assertFalse(any('repeat' in action for action in config['actions']))

    def test_missing_or_empty_failed_answer_cannot_create_history(self):
        for result in [{}, {'response_text': ''}, {'response_text': '   '}, None, {'response_text': 3}]:
            value = self.env.from_string(module.ANSWER_PRESENT).render(vision_result=result)
            self.assertEqual(value, 'False')
        self.assertEqual(self.env.from_string(module.ANSWER_PRESENT).render(vision_result={'response_text': 'A person carried a package.'}), 'True')

    def test_no_phone_notification_and_explicit_frontyard_camera(self):
        for config in self.configs:
            self.assertEqual(config['actions'][1]['data']['image_entity'], ['camera.frontyard'])
            self.assertNotIn('notify.', str(config))
            self.assertNotIn('all', config['triggers'][0]['entity_id'])
        with self.assertRaises(ValueError):
            module.build('fixture-provider', ['all'], ['binary_sensor.fixture_car'])

    def test_test_runs_are_identified_and_saved_answers_not_shortened(self):
        for config in self.configs:
            event = config['actions'][2]['choose'][0]['sequence'][0]['data']
            title = self.env.from_string(event['title']).render(qualification_test=True)
            self.assertTrue(title.startswith('TEST · '))
            answer = 'Detailed visible activity. ' * 50
            self.assertEqual(self.env.from_string(event['description']).render(vision_result={'response_text': answer}), answer)

if __name__ == '__main__':
    unittest.main()
