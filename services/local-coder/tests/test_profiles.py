"""Behavior contracts for the opt-in local profiles; no model or network calls."""
import fnmatch
from copy import deepcopy
import json
import os
import runpy
from pathlib import Path
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]
CONFIG = json.loads((SOURCE / 'opencode.json').read_text())
MODEL = 'edsys-local/edsys-qwen36-coder:latest'


def permitted(agent, tool):
    rules = list(CONFIG['permission'].items())
    rules += list(CONFIG['agent'][agent].get('permission', {}).items())
    return next((value for pattern, value in reversed(rules)
                 if fnmatch.fnmatchcase(tool, pattern)), 'allow') == 'allow'


class LocalProfileTests(unittest.TestCase):
    def test_native_local_provider_and_client_capacity(self):
        self.assertEqual(CONFIG['enabled_providers'], ['edsys-local'])
        self.assertEqual(CONFIG['model'], MODEL)
        self.assertEqual(CONFIG['small_model'], MODEL)
        self.assertNotIn('provider', CONFIG)
        self.assertEqual(set(CONFIG['providers']), {'edsys-local'})
        provider = CONFIG['providers']['edsys-local']
        self.assertEqual(provider['package'], '@opencode/ai/providers/openai-compatible')
        self.assertEqual(provider['compatibility'], {'maxTokensField': 'max_tokens'})
        self.assertEqual(set(provider['models']), {'edsys-qwen36-coder:latest'})
        self.assertEqual(provider['settings']['baseURL'], 'http://127.0.0.1:11435/v1')
        for name in ['timeout', 'headerTimeout', 'chunkTimeout']:
            self.assertEqual(provider['settings'][name], 7200000)
        model = provider['models']['edsys-qwen36-coder:latest']
        self.assertEqual(model['limit'], {'context': 262144, 'output': 32768})
        self.assertNotIn('modelID', model)  # API id remains the shared local alias.
        self.assertEqual(model['compatibility'], {'reasoningField': 'reasoning_content'})
        self.assertEqual(model['capabilities'], {'tools': True, 'input': ['text', 'image'], 'output': ['text']})

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
                     'edsys_search_records', 'edsys_search_history']:
            self.assertFalse(permitted('local-general', tool), tool)

    def test_code_keeps_browser_and_local_index_but_not_other_mcp_groups(self):
        for tool in ['browser_browser_navigate', 'browser_browser_snapshot',
                     'code_intelligence_code_intelligence_search_code']:
            self.assertTrue(permitted('local-code', tool), tool)
        for tool in ['desktop_desktop_screenshot', 'github_get_me',
                     'context7_resolve_library_id', 'microsoft_learn_microsoft_docs_search',
                     'edsys_search_records', 'edsys_search_history']:
            self.assertFalse(permitted('local-code', tool), tool)

    def test_primary_checkpoint_access_and_actual_child_denials(self):
        for agent in ['build', 'local-general', 'local-code']:
            self.assertTrue(permitted(agent, 'edsys_save_checkpoint'), agent)
        for agent in ['local-general', 'local-code']:
            self.assertTrue(permitted(agent, 'edsys_session_context'), agent)
        for agent in ['general', 'explore']:
            self.assertFalse(permitted(agent, 'edsys_save_checkpoint'), agent)
            self.assertFalse(permitted(agent, 'edsys_save_checkpoint'), agent)
            self.assertEqual(CONFIG['agent'][agent]['model'], MODEL)

    def test_optional_thinking_variants_do_not_change_default(self):
        model = CONFIG['providers']['edsys-local']['models']['edsys-qwen36-coder:latest']
        self.assertEqual({v['id']: v.get('settings', {}) for v in model['variants']},
                         {'normal': {}, 'fast': {'reasoningEffort': 'none'}})
        self.assertNotIn('reasoningEffort', model['body'])
        self.assertEqual(model['body'], {'temperature': 0.6, 'topP': 0.95})
        self.assertEqual(CONFIG['compaction'], {'auto': True})
        self.assertEqual(CONFIG['share'], 'disabled')

    def test_launcher_preserves_native_provider_for_all_tool_profiles(self):
        class Launched(Exception):
            pass
        common = {'edsys', 'code_intelligence', 'context7', 'microsoft_learn'}
        extras = {'all': set(CONFIG['mcp']['servers']), 'code': {'github'},
                  'browser': {'browser'}, 'desktop': {'desktop'}}
        def read(path, *args, **kwargs):
            return json.dumps(CONFIG) if path.name == 'opencode.json' else 'Fixture reviewed instructions'
        for profile, additional in extras.items():
            with self.subTest(profile=profile), \
                 mock.patch.dict(os.environ, {'PATH': '/usr/bin', 'OPENCODE_CONFIG': '/unreviewed',
                                             'OPENCODE_CONFIG_DIR': '/unreviewed',
                                             'OTEL_EXPORTER_OTLP_ENDPOINT': 'synthetic-test-only'}, clear=True), \
                 mock.patch('sys.argv', ['edsys-code', '--tools', profile, 'run', 'fixture']), \
                 mock.patch.object(Path, 'read_text', autospec=True, side_effect=read), \
                 mock.patch.object(Path, 'is_file', return_value=True), \
                 mock.patch.object(Path, 'is_mount', return_value=True), \
                 mock.patch.object(Path, 'exists', return_value=False), \
                 mock.patch.object(Path, 'mkdir'), mock.patch('os.umask'), \
                 mock.patch('os.execve', side_effect=Launched) as execute:
                with self.assertRaises(Launched):
                    runpy.run_path(str(SOURCE / 'edsys-code'), run_name='__main__')
                binary, command, env = execute.call_args.args
                self.assertTrue(binary.endswith('/opencode-2.2.7/opencode'))
                self.assertEqual(command, [binary, 'run', 'fixture'])
                forwarded = json.loads(env['OPENCODE_CONFIG_CONTENT'])
                self.assertEqual(forwarded['providers'], CONFIG['providers'])
                self.assertEqual({k: v for k, v in forwarded.items() if k != 'mcp'},
                                 {k: v for k, v in CONFIG.items() if k != 'mcp'})
                enabled = {name for name, server in forwarded['mcp']['servers'].items()
                           if not server['disabled']}
                self.assertEqual(enabled, common | additional)
                self.assertEqual(env['OPENCODE_DISABLE_PROJECT_CONFIG'], '1')
                self.assertEqual(env['OPENCODE_DISABLE_MODELS_FETCH'], '1')
                self.assertEqual(env['OPENCODE_PURE'], '0')
                self.assertEqual(env['PWD'], os.getcwd())
                self.assertNotIn('OPENCODE_CONFIG', env)
                self.assertNotIn('OPENCODE_CONFIG_DIR', env)
                self.assertFalse(any(name.startswith('OTEL_') for name in env))

    def test_shared_ollama_alias_keeps_its_original_generation_default(self):
        parameters = [line.split() for line in (SOURCE / 'Modelfile').read_text().splitlines()
                      if line.startswith('PARAMETER num_predict ')]
        self.assertEqual(parameters, [['PARAMETER', 'num_predict', '16384']])

    def test_live_catalog_contract_rejects_stale_budget_or_wrong_field(self):
        from validate_profiles import validate_provider_models
        providers = [{'id': 'edsys-local',
                      'package': '@opencode/ai/providers/openai-compatible',
                      'settings': deepcopy(CONFIG['providers']['edsys-local']['settings']),
                      'compatibility': {'maxTokensField': 'max_tokens'}}]
        models = [{'id': 'edsys-qwen36-coder:latest', 'providerID': 'edsys-local',
                   'limit': {'context': 262144, 'output': 32768},
                   'compatibility': {'reasoningField': 'reasoning_content'},
                   'variants': [{'id': 'normal', 'settings': {}},
                                {'id': 'fast', 'settings': {'reasoningEffort': 'none'}}]}]
        validate_provider_models(CONFIG, providers, models)
        stale = deepcopy(models)
        stale[0]['limit']['output'] = 16384
        with self.assertRaises(AssertionError):
            validate_provider_models(CONFIG, providers, stale)
        wrong_field = deepcopy(providers)
        wrong_field[0]['compatibility']['maxTokensField'] = 'max_completion_tokens'
        with self.assertRaises(AssertionError):
            validate_provider_models(CONFIG, wrong_field, models)
        wrong_endpoint = deepcopy(providers)
        wrong_endpoint[0]['settings']['baseURL'] = 'https://cloud.invalid/v1'
        with self.assertRaises(AssertionError):
            validate_provider_models(CONFIG, wrong_endpoint, models)
        cloud = deepcopy(providers)
        cloud[0]['id'] = 'openai'
        with self.assertRaises(AssertionError):
            validate_provider_models(CONFIG, cloud, models)


if __name__ == '__main__':
    unittest.main()
