#!/usr/bin/env python3
"""Install only this reviewed custom integration, without restarting HA."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parent
NAMES = ("__init__.py", "config_flow.py", "conversation.py", "knowledge.py", "pilot.py", "pilot_policy.py", "remote_policy.py", "media_remote.py", "bedroom_screen.py", "bedroom_comfort.py", "phil_voice.py", "sensor.py", "screen_protocol.py", "manifest.json")


def component_payload():
    folder=ROOT/'custom_components/edsys_voice_knowledge'
    return {name:(ROOT.parent/'pi5-bedroom-display/manual_protocol.py' if name=='screen_protocol.py' else folder/name).read_text() for name in NAMES}
WORKER = r'''
import hashlib,json,os,pathlib,sys,tempfile
p=json.load(sys.stdin);root=pathlib.Path('/config/custom_components/edsys_voice_knowledge')
allowed={'__init__.py','config_flow.py','conversation.py','knowledge.py','pilot.py','pilot_policy.py','remote_policy.py','media_remote.py','bedroom_screen.py','bedroom_comfort.py','phil_voice.py','sensor.py','screen_protocol.py','manifest.json'}
if set(p)!=allowed or root.is_symlink():raise RuntimeError('Unexpected component files or symlink')
if root.exists():
 manifest=json.loads((root/'manifest.json').read_text())
 if manifest.get('domain')!='edsys_voice_knowledge' or manifest.get('documentation')!='https://github.com/Jtclark314/edsys-infrastructure/tree/main/services/home-assistant-voice':raise RuntimeError('Existing unmanaged component; preserve it')
for name,text in p.items():
 if len(text)>65536:raise RuntimeError('Unexpected source size')
 if name.endswith('.py'):compile(text,name,'exec')
root.mkdir(mode=0o755,parents=True,exist_ok=True)
for name,text in p.items():
 fd,tmp=tempfile.mkstemp(prefix='.source-',dir=root)
 try:
  with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,0o644);os.replace(tmp,root/name)
 finally:
  pathlib.Path(tmp).unlink(missing_ok=True)
sys.path.insert(0,'/config')
from custom_components.edsys_voice_knowledge import conversation,config_flow,knowledge,bedroom_screen,bedroom_comfort,sensor
probe=conversation.EdSysVoice(type('Entry',(),{'entry_id':'fixture'})(),'conversation.fixture')
assert probe.supported_features==0
print(json.dumps({'installed_files':len(p),'native_imports':True,'local_priority_adapter':True}))
'''


def main():
    payload=component_payload()
    command=shlex.join(["qm","guest","exec","300","--pass-stdin","1","--timeout","30","--","docker","exec","-i","homeassistant","python3","-c",WORKER])
    process=subprocess.run(["ssh","-o","BatchMode=yes","-o","ClearAllForwardings=yes","pve-node3",command],input=json.dumps(payload),capture_output=True,text=True,timeout=40,check=True)
    result=json.loads(process.stdout)
    if result.get("exitcode") or result.get("out-truncated"):
        raise RuntimeError("Private component install/import failed; inspect before retry")
    print(result["out-data"].strip())


if __name__=="__main__":main()
