"""Disposable consistency, failure and security tests; no household calls."""
import copy
from datetime import datetime,timedelta,timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import common
import collect


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_online_wal_snapshot_is_logically_equal_and_source_unchanged(self):
        p=self.root/'live.sqlite';live=sqlite3.connect(p)
        live.execute('PRAGMA journal_mode=WAL');live.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY,value BLOB)')
        live.execute('INSERT INTO sample(value) VALUES(?)',(b'not published',));live.commit()
        before={q.name:common.sha(q.read_bytes()) for q in self.root.iterdir() if q.is_file() and not q.name.endswith('-shm')}
        raw,meta=common.sqlite_capture(p)
        self.assertTrue(meta['source_snapshot_parity']);collect.check_database(raw,meta)
        self.assertEqual(before,{q.name:common.sha(q.read_bytes()) for q in self.root.iterdir() if q.is_file() and not q.name.endswith('-shm')})
        live.close()

    def test_wal_grown_logical_size_is_bounded_before_memory_backup(self):
        p=self.root/'large.sqlite';live=sqlite3.connect(p)
        live.execute('PRAGMA journal_mode=WAL');live.execute('PRAGMA wal_autocheckpoint=0')
        live.execute('CREATE TABLE sample(value BLOB)');live.commit()
        live.execute('INSERT INTO sample VALUES(zeroblob(100000))');live.commit()
        self.assertLess(p.stat().st_size,20000)
        with patch.object(common,'MAX_FILE',20000):
            with self.assertRaisesRegex(ValueError,'sqlite_logical_size'):common.sqlite_capture(p)
        live.close()

    def test_snapshot_parity_tampering_rejected(self):
        p=self.root/'db';db=sqlite3.connect(p);db.execute('CREATE TABLE x(a)');db.commit();db.close()
        raw,meta=common.sqlite_capture(p);meta['source_logical_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'database_parity'):collect.check_database(raw,meta)

    def test_source_leaf_and_parent_symlinks_rejected(self):
        p=self.root/'actual';p.write_bytes(b'x');(self.root/'link').symlink_to(p)
        with self.assertRaisesRegex(ValueError,'symlink'):common.read_stable(self.root/'link')
        d=self.root/'folder';d.mkdir();(d/'a').write_bytes(b'x');(self.root/'alias').symlink_to(d)
        with self.assertRaisesRegex(ValueError,'symlink'):common.read_stable(self.root/'alias'/'a')

    def test_oversized_source_refused(self):
        p=self.root/'big';p.write_bytes(b'x'*20)
        with self.assertRaises(ValueError):common.read_stable(p,10)

    def test_source_player_pause_and_identity_changes_refused(self):
        x={'kodi_pid':'1','players':[{'playerid':1,'type':'video','item_sha256':'fixed','speed':0,'position':{'seconds':2}}]}
        collect.check_media(x,copy.deepcopy(x))
        for changed in [{'kodi_pid':'2'}, {'players':[]}, {'players':[{**x['players'][0],'speed':1}]}, {'players':[{**x['players'][0],'position':{'seconds':3}}]}]:
            with self.assertRaises(ValueError):collect.check_media(x,{**x,**changed})

    def test_traversal_duplicate_and_symlink_tar_members_refused(self):
        for name,link in [('../escape',False),('link',True)]:
            p=self.root/('archive'+str(link)+'.tar')
            with tarfile.open(p,'w') as t:
                e=tarfile.TarInfo(name)
                if link:e.type=tarfile.SYMTYPE;e.linkname='/private'
                else:e.size=1
                t.addfile(e,None if link else io.BytesIO(b'x'))
            with self.assertRaises(ValueError):collect.validate_archive(p,'abs')

    def test_failed_endpoint_keeps_last_good_and_removes_incoming(self):
        root=self.root/'stage';status=self.root/'status.json'
        def put(p):p.write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat()}))
        f={k:put for k in ['pi','nimo','abs']};f['validate']=lambda path,kind:{'source':kind,**json.loads(path.read_text())}
        first=collect.stage(root,status,f);old_nimo=first['endpoints']['nimo']['captured_at']
        def fail(p):p.write_bytes(b'partial');raise TimeoutError('offline')
        f['nimo']=fail
        second=collect.stage(root,status,f)
        self.assertEqual(second['endpoints']['nimo']['status'],'stale');self.assertEqual(second['endpoints']['nimo']['captured_at'],old_nimo)
        self.assertEqual(second['endpoints']['pi']['status'],'fresh');self.assertEqual(second['endpoints']['abs']['status'],'fresh')
        self.assertFalse(second['all_endpoints_fresh']);self.assertEqual(list((root/'.incoming').iterdir()),[])
        report=json.loads(status.read_text());self.assertEqual(report['status'],'degraded');self.assertIsNotNone(report['last_success'])
        self.assertEqual(collect.health(status)['bedroom_recovery_health'],'degraded')

    def test_invalid_archive_never_publishes_first_generation(self):
        root=self.root/'stage';status=self.root/'status.json'
        f={k:lambda p:p.write_bytes(b'x') for k in ['pi','nimo','abs']}
        f['validate']=lambda *_:(_ for _ in ()).throw(ValueError('bad'))
        self.assertIsNone(collect.stage(root,status,f))
        self.assertFalse((root/'current.json').exists());self.assertEqual(list((root/'generations').iterdir()),[])

    def test_health_marks_stale_future_and_missing_as_degraded(self):
        p=self.root/'health'
        for offset in [-27,1]:
            p.write_text(json.dumps({'status':'success','last_success':(datetime.now(timezone.utc)+timedelta(hours=offset)).isoformat()}))
            self.assertEqual(collect.health(p)['bedroom_recovery_health'],'degraded')
        self.assertEqual(collect.health(self.root/'missing')['bedroom_recovery_health'],'degraded')

    def test_transfer_bound_failure_has_no_raw_stderr(self):
        p=self.root/'transfer'
        with self.assertRaisesRegex(ValueError,'endpoint_output_bound'):
            collect.run_file([sys.executable,'-c','import sys;sys.stderr.write("private");print("x"*1000)'],p,limit=5,timeout=2)

    def test_peer_that_does_not_drain_stdin_still_times_out(self):
        with self.assertRaises(TimeoutError):
            collect.run_file([sys.executable,'-c','import time;time.sleep(10)'],self.root/'blocked',payload=b'x'*100000,timeout=.1)

    def test_zip_header_size_rejected_before_decompression(self):
        p=self.root/'bomb.zip'
        with zipfile.ZipFile(p,'w') as z:
            for n in collect.NIMO_NAMES|{'manifest.json'}:z.writestr(n,b'x')
        original=zipfile.ZipFile.infolist
        def oversized(z):
            entries=original(z);entries[0].file_size=common.MAX_FILE+1;return entries
        with patch.object(zipfile.ZipFile,'infolist',oversized),patch.object(zipfile.ZipFile,'read',side_effect=AssertionError('decompressed')):
            with self.assertRaisesRegex(ValueError,'zip_size'):collect.validate_archive(p,'nimo',{})

    def test_all_offline_preserves_current_pointer(self):
        root=self.root/'stage';status=self.root/'status.json'
        def put(p):p.write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat()}))
        f={k:put for k in ['pi','nimo','abs']};f['validate']=lambda path,kind:{'source':kind,**json.loads(path.read_text())}
        collect.stage(root,status,f);before=(root/'current.json').read_bytes()
        def fail(p):raise TimeoutError('offline')
        f.update({k:fail for k in ['pi','nimo','abs']})
        self.assertIsNone(collect.stage(root,status,f));self.assertEqual(before,(root/'current.json').read_bytes())

    def test_offline_endpoint_with_corrupt_prior_does_not_block_others(self):
        root=self.root/'stage';status=self.root/'status.json'
        def put(p):p.write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat()}))
        f={k:put for k in ['pi','nimo','abs']};f['validate']=lambda path,kind:{'source':kind,**json.loads(path.read_text())}
        first=collect.stage(root,status,f)
        (root/'generations'/first['generation']/'nimo.zip').write_bytes(b'corrupt')
        def fail(p):raise TimeoutError('offline')
        f['nimo']=fail;second=collect.stage(root,status,f)
        self.assertEqual(second['endpoints']['nimo']['status'],'missing')
        self.assertEqual(second['endpoints']['pi']['status'],'fresh');self.assertEqual(second['endpoints']['abs']['status'],'fresh')

    def test_missing_nimo_code_pins_rejected(self):
        members={n:b'x' for n in collect.NIMO_NAMES};members['manifest.json']=b'{}'
        m={'version':1,'kind':'nimo','source':'NIMO-LAPTOP','created_at':datetime.now(timezone.utc).isoformat(),
           'browser_profile_included':False,'files':{n:{'bytes':1,'sha256':common.sha(b'x')} for n in collect.NIMO_NAMES}}
        with self.assertRaisesRegex(ValueError,'nimo_missing_pins'):collect.check_manifest(m,members,'nimo',{})

    def test_logical_hash_deadline_is_enforced_in_python_rows(self):
        db=sqlite3.connect(':memory:');db.execute('CREATE TABLE x(a)');db.execute('INSERT INTO x VALUES(1)')
        with self.assertRaises(TimeoutError):common.logical_digest(db,0)
        db.close()

    def test_interrupted_attempt_never_leaves_prior_success_healthy(self):
        root=self.root/'stage';status=self.root/'status.json'
        def put(p):p.write_text(json.dumps({'created_at':datetime.now(timezone.utc).isoformat()}))
        f={k:put for k in ['pi','nimo','abs']};f['validate']=lambda path,kind:{'source':kind,**json.loads(path.read_text())}
        collect.stage(root,status,f);before=(root/'current.json').read_bytes()
        self.assertEqual(collect.health(status)['bedroom_recovery_health'],'healthy')
        def interrupted(p):raise KeyboardInterrupt
        f['pi']=interrupted
        with self.assertRaises(KeyboardInterrupt):collect.stage(root,status,f)
        self.assertEqual((root/'current.json').read_bytes(),before)
        self.assertEqual(json.loads(status.read_text())['status'],'collecting')
        self.assertEqual(collect.health(status)['bedroom_recovery_health'],'degraded')

    def test_success_without_all_endpoint_health_records_is_degraded(self):
        p=self.root/'status';now=datetime.now(timezone.utc).isoformat()
        for endpoints in [{},{'pi':{'status':'fresh','captured_at':now}}]:
            p.write_text(json.dumps({'status':'success','last_success':now,'endpoints':endpoints}))
            self.assertEqual(collect.health(p)['bedroom_recovery_health'],'degraded')

    def test_hard_incoming_exclusions_use_actual_configured_staging_root(self):
        script=ROOT.parents[1]/'scripts/backup/edsys-backup.sh'
        line=next(s for s in script.read_text().splitlines() if s.startswith('RESTIC_ARGS='))
        code='STAGING_DIR=/fixture\nEXISTING_INCLUDE_FILE=/i\nEXCLUDE_FILE=/e\nHOSTNAME_SHORT=x\nRUN_ID=x\n'+line+'\nprintf "%s\\0" "${RESTIC_ARGS[@]}"'
        args=subprocess.check_output(['bash','-c',code]).decode().split('\0')
        self.assertEqual(args.count('--exclude'),2)
        self.assertIn('/fixture/home-assistant/.incoming',args);self.assertIn('/fixture/bedroom-recovery/.incoming',args)


if __name__=='__main__':unittest.main()
