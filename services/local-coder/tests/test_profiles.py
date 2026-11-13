"""Behavior contracts for the opt-in local profiles; no model or network calls."""
import fnmatch
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1]
CONFIG = json.loads((SOURCE / 'opencode.json').read_text())
MODEL = 'edsys-local/edsys-qwen36-coder:latest'


def permitted(agent, tool):
    rules = list(CONFIG['permission'].items())
    rules += list(CONFIG['agent'][agent].get('permission', {}).items())
    return next((value for pattern, value in reversed(rules)
                 if fnmatch.fnmatchcase(tool, pattern)), 'allow') == 'allow'


class LocalProfileTests(unittest.TestCase):
    def test_local_provider_and_default_capacity_are_preserved(self):
        self.assertEqual(CONFIG['enabled_providers'], ['edsys-local'])
        self.assertEqual(CONFIG['model'], MODEL)
        self.assertEqual(CONFIG['small_model'], MODEL)
        self.assertEqual(set(CONFIG['provider']), {'edsys-local'})
        provider = CONFIG['provider']['edsys-local']
        self.assertEqual(provider['options']['baseURL'], 'http://127.0.0.1:11435/v1')
        for name in ['timeout', 'headerTimeout', 'chunkTimeout']:
            self.assertEqual(provider['options'][name], 7200000)
        model = provider['models']['edsys-qwen36-coder:latest']
        self.assertEqual(model['limit'], {'context': 262144, 'output': 16384})
        self.assertEqual(model['interleaved'], {'field': 'reasoning_content'})

    def test_new_profiles_are_opt_in_primary_agents_on_same_model(self):
        self.assertNotIn('default_agent', CONFIG)
        for name in ['local-general', 'local-code']:
            self.assertEqual(CONFIG['agent'][name]['mode'], 'primary')
            self.assertEqual(CONFIG['agent'][name]['model'], MODEL)
            self.assertNotIn('variant', CONFIG['agent'][name])
        self.assertEqual(CONFIG['agent']['build'], {})
        self.assertEqual(CONFIG['agent']['plan'], {})

    def test_native_tools_remain_available_in_both_profiles(self):
        for agent in ['local-general', 'local-code']:
            for tool in ['read', 'write', 'edit', 'bash', 'glob', 'grep', 'task',
                         'webfetch', 'websearch', 'todowrite', 'external_directory']:
                self.assertTrue(permitted(agent, tool), (agent, tool))
            self.assertFalse(permitted(agent, 'unreviewed_server_tool'))

    def test_general_hides_bulk_mcp_schemas(self):
        for tool in ['browser_browser_navigate', 'desktop_desktop_screenshot',
                     'code_intelligence_code_intelligence_search_code', 'github_get_me',
                     'context7_resolve_library_id', 'microsoft_learn_microsoft_docs_search',
                     'edsys_edsys_search_records', 'edsys_edsys_search_history']:
            self.assertFalse(permitted('local-general', tool), tool)

    def test_code_keeps_browser_and_local_index_but_not_other_mcp_groups(self):
        for tool in ['browser_browser_navigate', 'browser_browser_snapshot',
                     'code_intelligence_code_intelligence_search_code']:
            self.assertTrue(permitted('local-code', tool), tool)
        for tool in ['desktop_desktop_screenshot', 'github_get_me',
                     'context7_resolve_library_id', 'microsoft_learn_microsoft_docs_search',
                     'edsys_edsys_search_records', 'edsys_edsys_search_history']:
            self.assertFalse(permitted('local-code', tool), tool)

    def test_primary_checkpoint_access_and_actual_child_denials(self):
        for agent in ['build', 'local-general', 'local-code']:
            self.assertTrue(permitted(agent, 'edsys_edsys_save_checkpoint'), agent)
        for agent in ['local-general', 'local-code']:
            self.assertTrue(permitted(agent, 'edsys_edsys_session_context'), agent)
        for agent in ['general', 'explore']:
            self.assertFalse(permitted(agent, 'edsys_edsys_save_checkpoint'), agent)
            self.assertFalse(permitted(agent, 'edsys_save_checkpoint'), agent)
            self.assertEqual(CONFIG['agent'][agent]['model'], MODEL)

    def test_optional_thinking_variants_do_not_change_default(self):
        model = CONFIG['provider']['edsys-local']['models']['edsys-qwen36-coder:latest']
        self.assertEqual(model['variants'], {'normal': {}, 'fast': {'reasoningEffort': 'none'}})
        self.assertNotIn('reasoningEffort', model['options'])
        self.assertEqual(model['options'], {'temperature': 0.6, 'topP': 0.95})
        self.assertEqual(CONFIG['compaction'], {'auto': True, 'prune': False})
        self.assertEqual(CONFIG['share'], 'disabled')


if __name__ == '__main__':
    unittest.main()
