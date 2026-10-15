"""Fixed HDMI PCM player, supervised through a private parent pipe. No network."""
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time
import wave


def run(path):
    p=Path(path)
    if not re.fullmatch(r'/run/edsys-bedroom-display/reply-[a-f0-9-]{36}\.wav',str(p)) or p.is_symlink() or p.stat().st_uid!=os.getuid():raise ValueError('Unmanaged audio file')
    with wave.open(str(p),'rb') as w:
        if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())!=(1,2,24000,'NONE') or not 0<w.getnframes()<=1_440_000:raise ValueError('Unsupported PCM')
    parent=os.getppid();started=False;before=None;created=last=time.monotonic()
    child=subprocess.Popen(['/usr/bin/aplay','-q','-D','plughw:CARD=vc4hdmi0,DEV=0',str(p)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        while child.poll() is None:
            now=time.monotonic()
            if os.getppid()!=parent or now-last>7 or now-created>63:break
            if select.select([sys.stdin],[],[],0)[0]:
                data=os.read(0,64)
                if not data or data.strip(b'.'):break
                last=now
            try:
                text=Path('/proc/asound/card0/pcm0p/sub0/status').read_text()
                fields={k.strip():v.strip() for line in text.splitlines() if ':' in line for k,v in [line.split(':',1)]}
                position=int(fields.get('hw_ptr','0').strip())
                if fields.get('state','').strip()=='RUNNING' and int(fields.get('owner_pid','-1'))==child.pid:
                    if before is not None and position>before and not started:
                        started=True;print(json.dumps({'event':'started'}),flush=True)
                    before=position
            except (OSError,ValueError):pass
            if not started and now-created>3:break
            time.sleep(.025)
        ok=child.poll()==0 and started
    finally:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=1)
    detail=child.stderr.read(1024).lower()
    reason='busy' if b'busy' in detail else 'permission' if b'denied' in detail else 'start_unconfirmed' if not started else 'player_exit'
    print(json.dumps({'event':'finished','ok':ok,'reason':reason,'returncode':child.returncode}),flush=True)


if __name__=='__main__':
    try:run(sys.argv[1])
    except Exception:print(json.dumps({'event':'finished','ok':False,'reason':'helper_error'}),flush=True)
