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

from alsa_status import read_playback_status

START_TIMEOUT=3
PROGRESS_TIMEOUT=2


class PlaybackProgress:
    """Prove local ownership and continuing playback, never just process liveness."""
    def __init__(self,owner_pid,created):
        self.owner_pid=owner_pid;self.created=created;self.started=False
        self.position=None;self.last_progress=created

    def observe(self,fields,now):
        self.check(now)
        # ALSA playback keeps consuming queued frames while DRAINING; short
        # replies may enter this state before our first status observation.
        if fields.get('state') in {'RUNNING','DRAINING'}:
            owner=int(fields['owner_pid']);position=int(fields['hw_ptr'])
            if owner!=self.owner_pid or position<0:raise ValueError('Playback ownership changed')
            if self.position is not None and position<self.position:raise ValueError('Playback position reset')
            advanced=self.position is not None and position>self.position
            self.position=position
            if advanced:
                first=not self.started;self.started=True;self.last_progress=now
                return first
        return False

    def check(self,now):
        if not self.started and now-self.created>=START_TIMEOUT:raise TimeoutError('Playback start unconfirmed')
        if self.started and now-self.last_progress>=PROGRESS_TIMEOUT:raise TimeoutError('Playback progress stalled')


def run(path):
    p=Path(path)
    if not re.fullmatch(r'/run/edsys-bedroom-display/reply-[a-f0-9-]{36}\.wav',str(p)) or p.is_symlink() or p.stat().st_uid!=os.getuid():raise ValueError('Unmanaged audio file')
    with wave.open(str(p),'rb') as w:
        if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())!=(1,2,24000,'NONE') or not 0<w.getnframes()<=1_440_000:raise ValueError('Unsupported PCM')
    parent=os.getppid();created=last=time.monotonic();failure=None
    # Fail before launch if the named sink cannot be identified. Never rely on
    # card0: USB hotplug can change numeric card ordering.
    read_playback_status()
    child=subprocess.Popen(['/usr/bin/aplay','-q','-D','plughw:CARD=vc4hdmi0,DEV=0',str(p)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    progress=PlaybackProgress(child.pid,created)
    try:
        while child.poll() is None:
            now=time.monotonic()
            if os.getppid()!=parent or now-last>7 or now-created>63:
                failure='supervision_lost';break
            if select.select([sys.stdin],[],[],0)[0]:
                data=os.read(0,64)
                if not data or data.strip(b'.'):
                    failure='parent_closed';break
                last=now
            try:
                if progress.observe(read_playback_status(),now):
                    print(json.dumps({'event':'started'}),flush=True)
            except TimeoutError:
                failure='progress_stalled' if progress.started else 'start_unconfirmed';break
            except (OSError,ValueError,KeyError):
                failure='status_unavailable';break
            time.sleep(.025)
        ok=failure is None and child.poll()==0 and progress.started
    finally:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=1)
    detail=child.stderr.read(1024).lower()
    reason=failure or ('busy' if b'busy' in detail else 'permission' if b'denied' in detail else 'start_unconfirmed' if not progress.started else 'player_exit')
    print(json.dumps({'event':'finished','ok':ok,'reason':reason,'returncode':child.returncode}),flush=True)


if __name__=='__main__':
    try:run(sys.argv[1])
    except Exception:print(json.dumps({'event':'finished','ok':False,'reason':'helper_error'}),flush=True)
