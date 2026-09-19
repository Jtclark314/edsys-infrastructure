import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history import History, sync
from backup import database_copy, verify


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.source = self.root / 'source.sqlite'
        self.archive = self.root / 'archive/history.sqlite'
        self.memory = self.root / 'memory'
        with sqlite3.connect(self.source) as db:
            db.executescript('CREATE TABLE session(id TEXT,directory TEXT,title TEXT); CREATE TABLE message(id TEXT,session_id TEXT,data TEXT); CREATE TABLE part(id TEXT,session_id TEXT,data TEXT);')
            db.execute('INSERT INTO session VALUES (?,?,?)', ('s1', str(self.project), 'Fixture'))
            db.execute('INSERT INTO part VALUES (?,?,?)', ('p1', 's1', json.dumps({'type':'text','text':'Original remembered bluebird'})))

    def ingest(self):
        return sync(self.source, self.archive, self.memory)

    def test_versions_survive_edit_and_delete(self):
        self.ingest()
        with sqlite3.connect(self.source) as db:
            db.execute('UPDATE part SET data=?', (json.dumps({'type':'text','text':'Changed remembered magpie'}),))
        self.ingest()
        with sqlite3.connect(self.source) as db:
            db.execute('DELETE FROM part')
        result = self.ingest()
        self.assertEqual(result['added_versions'], 0)
        history = History(self.project, self.archive)
        for word in ('bluebird', 'magpie'):
            results = history.search(word)['results']
            self.assertEqual(len(results), 1)
            self.assertIn(word, history.read(results[0]['id'])['text'])
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)

    def test_project_scope_and_invalid_query(self):
        self.ingest()
        other = self.root / 'other'
        other.mkdir()
        history = History(other, self.archive)
        self.assertEqual(history.search('bluebird')['results'], [])
        row = history.search('bluebird', all_projects=True)['results'][0]
        with self.assertRaises(ValueError):
            history.read(row['id'])
        self.assertIn('bluebird', history.read(row['id'], all_projects=True)['text'])
        with self.assertRaises(ValueError):
            history.search('')

    def test_checkpoint_and_consistent_restore(self):
        folder = self.memory / 'fixture'
        folder.mkdir(parents=True)
        record = {'project':str(self.project),'revision':1,'checkpoint':{'decisions':['Choose emerald']}}
        (folder / 'checkpoint.json').write_text(json.dumps(record))
        (folder / 'revision-00000001.json').write_text(json.dumps(record))
        self.ingest()
        self.assertEqual(len(History(self.project,self.archive).search('emerald')['results']), 1)
        restored = self.root / 'restored.sqlite'
        database_copy(self.archive, restored)
        self.assertEqual(len(History(self.project,restored).search('emerald')['results']), 1)

    def test_overflow_survives_native_cleanup_and_restore(self):
        output = self.root / 'tool-output/tool_fixture'
        output.parent.mkdir()
        output.write_text('Earlier output\n' * 5000 + 'OVERFLOW-RETAINED-4981')
        with sqlite3.connect(self.source) as db:
            db.execute('INSERT INTO part VALUES (?,?,?)',
                       ('p2', 's1', json.dumps({'type': 'tool', 'state': {
                           'output': 'Truncated: ' + str(output),
                           'metadata': {'outputPath': str(output)}}})))
        self.ingest()
        output.unlink()
        self.ingest()
        restored = self.root / 'overflow-restored.sqlite'
        database_copy(self.archive, restored)
        history = History(self.project, restored)
        row = history.search('OVERFLOW-RETAINED-4981')['results'][0]
        self.assertEqual(row['kind'], 'tool-output')
        record = history.read(row['id'])
        self.assertGreater(record['total_characters'], 50000)
        final = history.read(row['id'], record['total_characters'] - 100)
        self.assertIn('OVERFLOW-RETAINED-4981', final['text'])


if __name__ == '__main__':
    unittest.main()
