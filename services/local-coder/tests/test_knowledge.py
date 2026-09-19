import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from knowledge import Records, ProjectMemory, digest, now

loader = importlib.machinery.SourceFileLoader('powershell_helper', str(SOURCE / 'edsys-powershell'))
spec = importlib.util.spec_from_loader(loader.name, loader)
ps = importlib.util.module_from_spec(spec)
loader.exec_module(ps)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()
        self.index = self.root / 'index.sqlite'
        self.create_index()
        self.records = Records(self.index, (self.docs,))

    def create_index(self):
        conn = sqlite3.connect(self.index)
        conn.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE sources(id INTEGER PRIMARY KEY,path TEXT,rel_path TEXT,title TEXT,source_group TEXT,mtime_epoch INTEGER,sha256 TEXT);
            CREATE TABLE chunks(source_id INTEGER,chunk_index INTEGER,text TEXT);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(source_id UNINDEXED,chunk_index UNINDEXED,title,path UNINDEXED,text);''')
        conn.executemany('INSERT INTO metadata VALUES (?,?)', [('schema_version','1'),('built_at',now()),('source_count','3'),('chunk_count','3')])
        for i, (name, title, body) in enumerate([
            ('node3', 'EdCore current role', '---\nstatus: current\nlast_audited: 2026-09-18\n---\nEdCore is pve-node3, a Proxmox node. Omarchy was destroyed.'),
            ('omarchy', 'EdCore historical role', '---\nstatus: historical-superseded-destroyed\n---\nEdCore Omarchy desktop was destroyed. Old procedure is history.'),
            ('nimo', 'Nimo controller', 'Nimo is a Windows controller. OpenCode runs on 9950x.')
        ], 1):
            path = self.docs / f'{name}.md'
            path.write_text(body)
            conn.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?)', (i,str(path),path.name,title,'reviewed',int(path.stat().st_mtime),digest(body)))
            conn.execute('INSERT INTO chunks VALUES (?,?,?)',(i,0,body))
            conn.execute('INSERT INTO chunks_fts VALUES (?,?,?,?,?)',(i,0,title,path.name,body))
        conn.commit()
        conn.close()

    def change_meta(self, key, value):
        with sqlite3.connect(self.index) as conn:
            conn.execute('UPDATE metadata SET value=? WHERE key=?',(value,key))

    def test_search_and_read_with_provenance(self):
        result = self.records.search('What is Nimo?')
        self.assertTrue(result['index']['fresh'])
        self.assertEqual(len(result['results']),1)
        row = result['results'][0]
        self.assertIn('nimo.md',row['path'])
        read = self.records.read(row['record_id'])
        self.assertIn('Windows controller',read['chunks'][0]['text'])
        self.assertIn('not a live',read['source']['evidence_type'])

    def test_history_is_explicit(self):
        result = self.records.search('EdCore')
        self.assertTrue(result['historical_chunks_excluded'])
        self.assertFalse(any(r['historical'] for r in result['results']))
        self.assertTrue(any(r['historical'] for r in self.records.search('EdCore',include_history=True)['results']))
        self.assertEqual(result['results'][0]['last_audited'],'2026-09-18')

    def test_unknown_does_not_fabricate(self):
        self.assertEqual(self.records.search('unregistered-zebra-device')['results'],[])

    def test_stale_missing_and_unknown_schema_fail(self):
        self.change_meta('built_at',(datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat())
        self.assertFalse(self.records.status()['fresh'])
        with self.assertRaisesRegex(ValueError,'stale'):
            self.records.search('Nimo')
        self.change_meta('schema_version','2')
        self.assertFalse(self.records.status()['available'])
        self.index.unlink()
        self.assertFalse(self.records.status()['available'])

    def test_source_drift_fails_and_atomic_index_replacement_seen(self):
        (self.docs/'nimo.md').write_text('Changed device role')
        with self.assertRaisesRegex(ValueError,'changed since'):
            self.records.search('Nimo')
        self.index.unlink()
        self.create_index()
        self.assertTrue(self.records.search('Nimo')['results'])

    def test_source_symlink_escape_rejected(self):
        body = (self.docs/'nimo.md').read_text()
        outside = self.root/'outside.md'
        outside.write_text(body)
        (self.docs/'nimo.md').unlink()
        (self.docs/'nimo.md').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'outside'):
            self.records.search('Nimo')

    def test_query_only_and_input_bounds(self):
        with self.records.connect() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM sources")
        self.records.search('"Nimo" OR NEAR() --')
        for kwargs in ({'limit':7},{'query':'x'*301}):
            with self.assertRaises(ValueError):
                self.records.search(**({'query':'Nimo'}|kwargs))
        with self.assertRaises(ValueError):
            self.records.read('../../etc/passwd')


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        self.project.mkdir()
        self.state = self.root/'private'
        self.memory = ProjectMemory(self.project,self.state)
        (self.project/'app.py').write_text('print(1)\n')

    def save(self, **kwargs):
        payload = {'expected_revision':0,'outcome':'Fix the app','completed':['Inspected app.py'],
                   'decisions':[],'next_steps':['Run tests'],'blockers':[],'evidence_paths':['app.py']}
        payload.update(kwargs)
        return self.memory.save(**payload)

    def test_restart_project_isolation_and_permissions(self):
        self.save()
        again = ProjectMemory(self.project,self.state).read()
        self.assertEqual(again['revision'],1)
        self.assertTrue(again['checkpoint']['evidence'][0]['unchanged'])
        other = self.root/'other'
        other.mkdir()
        self.assertIsNone(ProjectMemory(other,self.state).read()['checkpoint'])
        self.assertEqual(self.memory.file.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.memory.folder.stat().st_mode & 0o777,0o700)
        self.assertEqual(set(p.name for p in self.project.iterdir()),{'app.py'})

    def test_conflict_retention_and_changed_evidence(self):
        self.save()
        with self.assertRaisesRegex(ValueError,'another session'):
            self.save()
        for n in range(1,24):
            self.save(expected_revision=n)
        self.assertEqual(len(list(self.memory.folder.glob('revision-*.json'))),23)
        (self.project/'app.py').write_text('print(2)')
        self.assertFalse(self.memory.read()['checkpoint']['evidence'][0]['unchanged'])
        (self.project/'app.py').unlink()
        self.assertFalse(self.memory.read()['checkpoint']['evidence'][0]['unchanged'])

    def test_evidence_and_secret_boundaries(self):
        for paths in ([],['../outside'],['.env'],['missing.py'],['/etc/passwd']):
            with self.assertRaises(ValueError):
                self.save(evidence_paths=paths)
        (self.project/'link.py').symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):
            self.save(evidence_paths=['link.py'])
        with self.assertRaisesRegex(ValueError,'credential'):
            self.save(outcome='password='+'abcdefghijk')
        self.save(completed=[],evidence_paths=[],decisions=['User selected blue theme'])

    def test_guidance_root_and_truncation(self):
        (self.project/'AGENTS.md').write_text('Follow project rules.\n'+'x'*17000)
        result = self.memory.guidance()
        self.assertTrue(result[0]['truncated'])
        self.assertEqual(len(result[0]['text']),16000)
        (self.project/'AGENTS.md').unlink()
        (self.project/'AGENTS.md').symlink_to(self.root/'other.md')
        (self.root/'other.md').write_text('external')
        with self.assertRaisesRegex(ValueError,'escapes'):
            self.memory.guidance()


class ProfileTests(unittest.TestCase):
    def test_config_context_terminal_and_child_memory_boundary(self):
        cfg=json.loads((SOURCE/'opencode.json').read_text())
        self.assertEqual(cfg['permission']['bash'],'allow')
        self.assertEqual(cfg['permission']['external_directory'],'allow')
        self.assertEqual(cfg['agent']['general']['permission']['edsys_save_checkpoint'],'deny')
        self.assertEqual(cfg['agent']['explore']['permission']['edsys_save_checkpoint'],'deny')
        self.assertIn('edsys',cfg['mcp'])
        for path in cfg['instructions']:
            self.assertTrue(Path(path).is_file())

    def test_powershell_command_no_interpolation(self):
        argv=ps.remote_command('nimo','powershell.exe')
        self.assertIn('StrictHostKeyChecking=yes',argv)
        self.assertEqual(argv[-2],'nimo-laptop')
        self.assertNotIn('Bypass',argv[-1])
        self.assertTrue(argv[-1].endswith('; exit $LASTEXITCODE'))
        self.assertFalse(ps.remote_command('pve-node3','pwsh')[-1].endswith('; exit $LASTEXITCODE'))
        for host in ('-oProxyCommand=bad','a;echo bad','a$(date)','a\nb'):
            with self.assertRaises(ValueError):
                ps.remote_command(host,'pwsh')


if __name__ == '__main__':
    unittest.main()
