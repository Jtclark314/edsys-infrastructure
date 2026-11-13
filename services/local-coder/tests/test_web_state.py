import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_state


class WebStateTests(unittest.TestCase):
    def test_v1_checks_every_project_before_claiming_idle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'web').mkdir()
            (root / 'web/proxy.json').write_text(json.dumps({'backendPassword':'synthetic'}))
            (root / 'data/opencode').mkdir(parents=True)
            with sqlite3.connect(root / 'data/opencode/opencode.db') as db:
                db.execute('CREATE TABLE session(directory TEXT)')
                db.executemany('INSERT INTO session VALUES (?)', [('/project/a',),('/project/b',)])
            seen = []

            def response(request, timeout):
                url = request.full_url
                seen.append(url)
                if url.endswith('/api/info'):
                    raise ValueError('V1 has no V2 info endpoint')
                if url.endswith('/global/health'):
                    data = {'version':'1.18.31'}
                elif '%2Fproject%2Fb' in url:
                    data = {'busy-session': {'type':'busy'}}
                else:
                    data = {}
                return io.BytesIO(json.dumps(data).encode())

            with patch.object(web_state, 'ROOT', root), patch.object(web_state, 'urlopen', response):
                result = web_state.status()
            self.assertFalse(result['idle'])
            self.assertEqual(result['busy']['busy-session']['directory'], '/project/b')
            self.assertEqual(sum('/session/status?' in url for url in seen), 2)

    def test_v2_uses_process_wide_active_session_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'web').mkdir()
            (root / 'web/proxy.json').write_text(json.dumps({'backendPassword':'synthetic'}))

            def response(request, timeout):
                data = {'version':'2.2.7'} if request.full_url.endswith('/api/info') else {'data':{'active':{}}}
                return io.BytesIO(json.dumps(data).encode())

            with patch.object(web_state, 'ROOT', root), patch.object(web_state, 'urlopen', response):
                self.assertFalse(web_state.status()['idle'])

    def test_terminal_detection_excludes_isolated_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for pid, data in [('123', web_state.ROOT / 'data'), ('124', root / 'isolated-data')]:
                process = root / pid
                process.mkdir()
                (process / 'cmdline').write_bytes(b'/apps/opencode\0run\0--standalone\0private prompt\0')
                (process / 'environ').write_bytes(b'XDG_DATA_HOME=' + str(data).encode() + b'\0')
                (process / 'cwd').symlink_to(root, target_is_directory=True)
            clients = web_state.terminal_clients(root)
            self.assertEqual([row['pid'] for row in clients], [123])
            self.assertNotIn('private prompt', json.dumps(clients))

    def test_web_idle_does_not_hide_separate_terminal_work(self):
        def response(path, port=4096):
            return {'version':'2.2.7'} if path == '/api/info' else {'data':{}}
        with patch.object(web_state, 'read_api', response), patch.object(web_state, 'terminal_clients', return_value=[{'pid':123}]):
            result = web_state.status()
        self.assertFalse(result['idle'])
        self.assertEqual(result['busy'], {})
        self.assertEqual(result['terminal_clients'], [{'pid':123}])


if __name__ == '__main__':
    unittest.main()
