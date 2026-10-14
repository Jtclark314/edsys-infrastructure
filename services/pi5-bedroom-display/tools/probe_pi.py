#!/usr/bin/env python3
"""Read-only SSH baseline. Full output goes only to an explicit private path."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

REMOTE = r'''
import json, pathlib, subprocess
def run(*args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=10)
    return {'code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()[:200]}
r={'hostname':run('hostname'),'uptime':run('uptime','-p'),
   'model':pathlib.Path('/proc/device-tree/model').read_bytes().rstrip(b'\0').decode(),
   'power':run('vcgencmd','get_throttled'),'temperature':run('vcgencmd','measure_temp'),
   'ethernet':{k:pathlib.Path('/sys/class/net/eth0/'+k).read_text().strip() for k in ['operstate','carrier','speed']},
   'capture':run('arecord','-l'),'usb':run('lsusb'),
   'failed_units':run('systemctl','--failed','--plain','--no-legend'),'drm':[]}
for p in pathlib.Path('/sys/class/drm').glob('card*-HDMI-A-*'):
    d={'connector':p.name,'status':(p/'status').read_text().strip(),'modes':sorted(set((p/'modes').read_text().splitlines()))}
    b=(p/'edid').read_bytes();d['edid_bytes']=len(b)
    if len(b)>=128:
        for i in [54,72,90,108]:
            q=b[i:i+18]
            if q[:5]==b'\0\0\0\xfc\0':d['display_name']=q[5:].decode('ascii','replace').strip()
        q=b[54:72]
        if q[0] or q[1]:d['preferred_active']=[q[2]+((q[4]>>4)<<8),q[5]+((q[7]>>4)<<8)]
    r['drm'].append(d)
uid=run('id','-u','edsys-voice')['stdout']
r['voice_gate']=run('sudo','-n','-u','edsys-voice','env','XDG_RUNTIME_DIR=/run/user/'+uid,
 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+uid+'/bus','/usr/local/lib/edsys-voice/run.py','--check')
print(json.dumps(r))
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--output', required=True, type=Path)
    a=p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', a.host):
        p.error('Use an SSH alias or hostname, without options or a username')
    destination=a.output.resolve()
    if destination.is_relative_to(Path(__file__).resolve().parents[3]):
        p.error('Private runtime evidence must be outside the source checkout')
    r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',a.host,'python3','-'],
                     input=REMOTE,text=True,capture_output=True,timeout=60,check=True)
    data=json.loads(r.stdout)
    data['observed_at']=datetime.now(timezone.utc).isoformat()
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(destination, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,'w') as f:json.dump(data,f,indent=2);f.write('\n')
    connected=[d for d in data['drm'] if d['status']=='connected']
    print(json.dumps({'ethernet':data['ethernet'], 'connected_displays':connected,
                      'voice_gate_passed':data['voice_gate']['code']==0,
                      'private_evidence_saved':str(destination)},indent=2))


if __name__=='__main__':
    main()
