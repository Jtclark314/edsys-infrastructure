"""One-use HDMI handoff and guarded Pi playback lease. No URL or HA access."""
from array import array
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
import wave

import xbmc
import xbmcgui
from manual_protocol import REPLY_PROPERTY,validate
from model import PlaybackSnapshot,PlaybackLease

ROOT=Path(__file__).resolve().parent
RUNTIME=Path('/run/edsys-bedroom-display')


class KodiBackend:
    def rpc(self,method,**params):
        data=json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params})))
        if 'error' in data:raise RuntimeError('Native player call failed')
        return data['result']

    def snapshot(self,revision,epoch):
        players=self.rpc('Player.GetActivePlayers')
        if not players:return None
        if len(players)!=1 or players[0].get('playerid')!=1 or players[0].get('type')!='video' or players[0].get('playertype','internal')!='internal':raise RuntimeError('Unsupported foreground player')
        p=self.rpc('Player.GetProperties',playerid=1,properties=['speed','time'])
        if p['speed'] not in (0,1):raise RuntimeError('Unsupported playback speed')
        item=self.rpc('Player.GetItem',playerid=1,properties=['file'])['item']['file']
        t=p['time'];position=t['hours']*3600+t['minutes']*60+t['seconds']+t['milliseconds']/1000
        return PlaybackSnapshot('pi-video',str(epoch),hashlib.sha256(item.encode()).hexdigest(),p['speed']==1,position,revision)

    def pause(self):
        if self.rpc('Player.PlayPause',playerid=1,play=False).get('speed')!=0:raise RuntimeError('Pause uncertain')

    def resume(self):
        if self.rpc('Player.PlayPause',playerid=1,play=True).get('speed')!=1:raise RuntimeError('Resume uncertain')

    def gain(self):
        p=self.rpc('Application.GetProperties',properties=['volume','muted'])
        volume=p['volume']
        if type(volume) is not int or not 0<=volume<=100:raise RuntimeError('Unsupported volume')
        return 0 if p['muted'] else volume/100

    def suspend(self):
        device=self.rpc('Settings.GetSettingValue',setting='audiooutput.audiodevice')['value']
        passthrough=self.rpc('Settings.GetSettingValue',setting='audiooutput.passthrough')['value']
        if not device.startswith('ALSA:hdmi:CARD=vc4hdmi0,DEV=0|') or passthrough:raise RuntimeError('HDMI audio mapping changed')
        xbmc.audioSuspend()
        # Native suspend is asynchronous on this GBM/ALSA build. Wait for the
        # device to be released; never race aplay against Kodi's closing sink.
        until=time.monotonic()+2
        while time.monotonic()<until:
            if Path('/proc/asound/card0/pcm0p/sub0/status').read_text().strip()=='closed':return
            time.sleep(.025)
        raise RuntimeError('HDMI sink did not release')

    def restore(self):xbmc.audioResume()

    def launch(self,path):
        return subprocess.Popen(['/usr/bin/python3',str(ROOT/'reply_player.py'),str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,start_new_session=True)


class ReplyAudio:
    def __init__(self,backend=None,runtime=RUNTIME):
        self.backend=backend or KodiBackend();self.runtime=Path(runtime)
        self.status={'version':1,'state':'idle','started':False,'paused_by_us':False}
        self.current=None;self.path=None;self.process=None;self.suspended=False;self.lease=None
        self.revision=0;self.epoch=0;self.pending_pause=False;self.events=queue.Queue(maxsize=4)
        self.marker=self.runtime/'reply-engine.json';self.last_heartbeat=0
        self.player_token=None
        if self.marker.exists():
            # Only this add-on writes this owner-only runtime recovery marker.
            if self.marker.is_symlink() or self.marker.stat().st_uid!=os.getuid():raise RuntimeError('Unsafe audio marker')
            self.backend.restore();self.marker.unlink()
        for p in self.runtime.glob('reply-*.wav'):p.unlink(missing_ok=True)
        self.publish()

    def publish(self):
        xbmcgui.Window(10000).setProperty(REPLY_PROPERTY,json.dumps(self.status,separators=(',',':')))

    def cleanup_file(self):
        if self.path:self.path.unlink(missing_ok=True);self.path=None

    def restore(self):
        if self.suspended:
            self.backend.restore();self.suspended=False;self.marker.unlink(missing_ok=True)

    def abort(self,reason='cancelled'):
        self.player_token=None
        if self.process:
            try:self.process.stdin.close()
            except (OSError,ValueError):pass
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:os.killpg(self.process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL)
            self.process=None
        try:self.restore()
        finally:
            self.cleanup_file();self.pending_pause=False
            self.marker.unlink(missing_ok=True)
            if self.lease:self.lease.manual_change()
            self.status.update(state=reason,resume_result='forfeited');self.publish()

    def notify(self,method):
        if method not in {'Player.OnPause','Player.OnResume','Player.OnSeek','Player.OnStop','Player.OnPlay','Player.OnAVStart','Player.OnSpeedChanged'}:return
        self.revision+=1
        if method in {'Player.OnStop','Player.OnAVStart'}:self.epoch+=1
        if self.pending_pause and method=='Player.OnPause':
            self.pending_pause=False
            now=self.backend.snapshot(self.revision,self.epoch)
            if not self.lease or not now or not self.lease.acknowledge_pause(now):self.abort('failed')
        elif self.lease and self.status['state'] in {'pause_wait','starting','playing','finished'}:
            self.abort('interrupted')

    def receive(self,m,owner):
        validate(m)
        if not owner or m['request_id']!=owner:return
        command=m['command']
        if command=='reply_prepare':
            if m['sequence']!=0 or self.current==m['request_id']:return
            self.abort();self.current=m['request_id'];self.audio_ref=m['audio_ref'];self.sequence=0
            self.size,self.digest=m['size'],m['sha256'];self.received=0;self.lease=None
            self.path=self.runtime/('reply-'+self.audio_ref+'.wav')
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
            self.status={'version':1,'request_id':self.current,'audio_ref':self.audio_ref,'sequence':0,
                         'state':'receiving','received':0,'started':False,'paused_by_us':False}
        else:
            if (m['request_id'],m['audio_ref'],m['sequence'])!=(self.current,getattr(self,'audio_ref',None),getattr(self,'sequence',-1)+1):return
            self.sequence=m['sequence'];self.status['sequence']=self.sequence
            if command=='reply_chunk' and self.status['state']=='receiving':
                raw=base64.b64decode(m['data'],validate=True)
                if m['offset']!=self.received or self.received+len(raw)>self.size:raise ValueError('Reply chunk order')
                with self.path.open('ab') as f:f.write(raw)
                self.received+=len(raw);self.status['received']=self.received
            elif command=='reply_commit' and self.status['state']=='receiving':
                raw=self.path.read_bytes()
                if len(raw)!=self.size or hashlib.sha256(raw).hexdigest()!=self.digest:raise ValueError('Reply digest mismatch')
                with wave.open(str(self.path),'rb') as w:
                    if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())!=(1,2,24000,'NONE') or not 0<w.getnframes()<=1_440_000 or len(w.readframes(w.getnframes()))!=w.getnframes()*2:raise ValueError('Unsupported reply WAV')
                gain=self.backend.gain()
                if gain!=1:
                    with wave.open(str(self.path),'rb') as w:samples=array('h',w.readframes(w.getnframes()))
                    if __import__('sys').byteorder!='little':samples.byteswap()
                    for i,v in enumerate(samples):samples[i]=round(v*gain)
                    if __import__('sys').byteorder!='little':samples.byteswap()
                    with wave.open(str(self.path),'wb') as w:
                        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(samples.tobytes())
                before=self.backend.snapshot(self.revision,self.epoch)
                self.lease=PlaybackLease(before) if before else None
                self.status['state']='pause_wait';self.deadline=time.monotonic()+2
                if before and before.playing:
                    self.pending_pause=True;self.backend.pause()
                else:self.start()
            elif command=='reply_release' and self.status['state']=='finished':
                self.status['resume_result']='not_owned'
                if m['resume'] and self.lease:
                    current=self.backend.snapshot(self.revision,self.epoch)
                    if current and self.lease.take_resume_permission(current):
                        self.backend.resume();self.status['resume_result']='resumed'
                    else:self.status['resume_result']='forfeited'
                self.lease=None;self.status['state']='released'
            elif command=='reply_cancel':self.abort()
            else:return
        self.publish()

    def start(self):
        self.marker.write_text(json.dumps({'owner':os.getpid()}));self.marker.chmod(0o600)
        self.status['stage']='engine_suspend'
        self.suspended=True;self.backend.suspend()
        self.status['stage']='player_launch'
        self.process=self.backend.launch(self.path);self.status.update(state='starting',paused_by_us=bool(self.lease and self.lease.paused_by_us))
        process=self.process
        token=self.player_token=object()
        def reader():
            try:
                for line in process.stdout:
                    if len(line)>256:break
                    self.events.put_nowait((token,json.loads(line)))
            except (ValueError,queue.Full,OSError):pass
            finally:
                try:self.events.put_nowait((token,{'event':'eof'}))
                except queue.Full:pass
        threading.Thread(target=reader,daemon=True).start();self.last_heartbeat=0;self.deadline=time.monotonic()+65

    def tick(self,valid):
        if not self.current:return
        try:
            if not valid and self.status['state'] not in {'idle','released','cancelled','failed','interrupted'}:self.abort();return
            if self.status['state']=='pause_wait':
                if not self.pending_pause and self.lease and self.lease.paused_by_us:self.start()
                elif time.monotonic()>=self.deadline:self.abort('failed')
            if self.process and time.monotonic()-self.last_heartbeat>=1:
                self.process.stdin.write('.');self.process.stdin.flush();self.last_heartbeat=time.monotonic()
            while not self.events.empty():
                token,event=self.events.get_nowait()
                if token is not self.player_token:continue
                if event.get('event')=='started':self.status.update(state='playing',started=True)
                elif event.get('event')=='finished':
                    if self.process:
                        self.process.stdin.close();self.process.wait(timeout=1);self.process=None
                    self.restore();self.cleanup_file()
                    if event.get('ok') and self.status['started']:self.status['state']='finished';self.deadline=time.monotonic()+7
                    else:
                        self.status['failure']=event.get('reason','player_failed');self.status['player_returncode']=event.get('returncode');self.abort('failed')
                elif event.get('event')=='eof' and self.status['state'] in {'starting','playing'}:self.abort('failed')
            if self.status['state'] in {'starting','playing','finished'} and time.monotonic()>self.deadline:self.abort('failed')
            self.publish()
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            self.status['failure']='subinterpreter' if 'subinterpreter' in str(error) else type(error).__name__;self.abort('failed')
