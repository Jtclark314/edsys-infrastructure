import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('provision_intelligence_local',Path(__file__).with_name('provision_intelligence_local.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class ProvisionTests(unittest.TestCase):
    def test_worker_syntax_and_cpu_only_boundary(self):
        compile(p.WORKER,'broker_worker','exec')
        self.assertIn("'api_base':'http://ollama-coder:11434'",p.WORKER)
        self.assertIn("allowed=[ALIAS,'edsys-chat-local','edsys-embeddings-fast']",p.WORKER)
        self.assertNotIn("'/key/generate'",p.WORKER)
        self.assertNotIn("openai/",p.WORKER)
    def test_preflight_records_only_bounded_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            receipt={'preflight':True,'before':'synthetic-fingerprint'}
            completed=SimpleNamespace(returncode=0,stdout='OBS_RESULT '+json.dumps(receipt),stderr='')
            with patch('sys.argv',['provision','--private-state',d]),patch.object(p.subprocess,'run',return_value=completed) as run:
                p.main()
            self.assertEqual(json.loads((Path(d)/'preflight.json').read_text()),receipt)
            self.assertNotIn('api_key',run.call_args.kwargs['input'])
            self.assertFalse((Path(d)/'attempted.json').exists())
    def test_durable_attempt_prevents_replay(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'attempted.json').write_text('{}')
            with patch('sys.argv',['provision','--private-state',d,'--apply']),patch.object(p.subprocess,'run') as run:
                with self.assertRaises(RuntimeError):p.main()
                run.assert_not_called()
if __name__=='__main__':unittest.main()
