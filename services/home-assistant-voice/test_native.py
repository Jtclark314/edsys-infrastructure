#!/usr/bin/env python3
"""Test the candidate in a temporary Core directory; never install or call devices."""
import json
from pathlib import Path
import shlex
import subprocess
from install_knowledge import NAMES

ROOT=Path(__file__).resolve().parent
WORKER=r'''
import json,pathlib,sys,tempfile,unittest
p=json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix='edsys-voice-candidate-') as tmp:
 root=pathlib.Path(tmp);package=root/'custom_components'/'edsys_voice_knowledge';package.mkdir(parents=True)
 (root/'custom_components'/'__init__.py').write_text('')
 for name,text in p['component'].items():
  if '/' in name or len(text)>65536:raise RuntimeError('Unexpected source')
  (package/name).write_text(text)
 tests=root/'checks';tests.mkdir()
 for name,text in p['tests'].items():(tests/name).write_text(text)
 sys.path[:0]=[str(root),str(tests)]
 suite=unittest.defaultTestLoader.discover(str(tests),pattern='native_*_checks.py')
 result=unittest.TextTestRunner(verbosity=1).run(suite)
 print(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'candidate_installed':False}))
 if not result.wasSuccessful():sys.exit(1)
'''

if __name__=='__main__':
    component=ROOT/'custom_components/edsys_voice_knowledge'
    payload={'component':{n:(component/n).read_text() for n in NAMES},
             'tests':{p.name:p.read_text() for p in (ROOT/'tests').glob('native_*_checks.py')}}
    command=shlex.join(['qm','guest','exec','300','--pass-stdin','1','--timeout','90','--','docker','exec','-i','homeassistant','python3','-c',WORKER])
    proc=subprocess.run(['ssh','-o','BatchMode=yes','-o','ClearAllForwardings=yes','pve-node3',command],input=json.dumps(payload),capture_output=True,text=True,timeout=105,check=True)
    result=json.loads(proc.stdout)
    print(result.get('out-data',''));print(result.get('err-data',''))
    if result.get('exitcode') or result.get('out-truncated'):raise SystemExit('Candidate native checks failed')
