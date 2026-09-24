import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('controller', Path(__file__).parents[1] / 'controller.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class TransportTests(unittest.TestCase):
    def test_powershell_literal(self):
        self.assertEqual(c.literal("C:/User's/$file"), "'C:/User''s/$file'")

    def test_payload_rejections(self):
        for action, payload in [('other', {}), ('ui', {'arguments': ['a\0b']}),
                                ('ui', {'arguments': 'shell string'}),
                                ('powershell', {'script': 'x' * 33000})]:
            with self.assertRaises(ValueError):
                c.validate_request(action, payload)

    def test_artifact_traversal_rejected_before_transport(self):
        for name in ('../secret.png', '/tmp/out.png', 'a' * 32 + '.exe'):
            with self.assertRaises(ValueError):
                c.download_artifact(name)

    def test_timeout_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'STATE', Path(tmp)), \
                patch.object(c, 'config', return_value={'remote_root': "C:/User's/Control"}), \
                patch.object(c, 'remote', side_effect=RuntimeError('timeout')) as remote:
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                c.request('ui', arguments=['invoke', 'button'])
            remote.assert_called_once()
            script = remote.call_args.args[0]
            self.assertIn("$r='C:/User''s/Control'", script)
            self.assertIn('outcome unknown', script)

    def test_mismatched_response_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'STATE', Path(tmp)), \
                patch.object(c, 'config', return_value={'remote_root': 'C:/Control'}), \
                patch.object(c, 'remote', return_value='{"id":"wrong","ok":true,"value":{}}'):
            with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
                c.request('status')


if __name__ == '__main__':
    unittest.main()
