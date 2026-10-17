"""Real HDMI handoff state machine with a fake player/process, no device I/O."""
import base64
from dataclasses import replace
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4
import wave

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'kodi')]
from model import PlaybackSnapshot
from manual_protocol import validate,seal,unseal,REPLY_CHUNK

spec=importlib.util.spec_from_file_location('reply_fixture',ROOT/'kodi/reply_audio.py')
module=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'xbmc':Mock(),'xbmcgui':Mock()}):spec.loader.exec_module(module)


def wav():
    b=io.BytesIO()
    with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\x00\x20'*24000)
    return b.getvalue()


class Backend:
    def __init__(self,playing=True):
        self.current=PlaybackSnapshot('pi-video','0','same-item',playing,25.0,0)
        self.calls=[];self.level=1
    def snapshot(self,revision,epoch):return replace(self.current,revision=revision,session=str(epoch)) if self.current else None
    def gain(self):return self.level
    def pause(self):self.calls.append('pause');self.current=replace(self.current,playing=False)
    def resume(self):self.calls.append('resume');self.current=replace(self.current,playing=True)
    def suspend(self):self.calls.append('suspend')
    def restore(self):self.calls.append('restore')
    def launch(self,path):
        self.calls.append('launch');self.process=SimpleNamespace(stdin=io.StringIO(),stdout=[],wait=lambda **_:0)
        return self.process


class ReplyTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(module.threading,'Thread');p.start();self.addCleanup(p.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.backend=Backend();self.reply=module.ReplyAudio(self.backend,self.tmp.name)
        self.key=str(uuid4());self.ref=str(uuid4());self.seq=-1
    def send(self,command,**data):
        self.seq+=1
        import time
        now=time.time()
        m=dict(version=1,request_id=self.key,audio_ref=self.ref,sequence=self.seq,issued_at=now,expires_at=now+7,command='reply_'+command,**data)
        self.reply.receive(m,self.key);return m
    def prepare(self):
        b=wav();self.send('prepare',size=len(b),sha256=hashlib.sha256(b).hexdigest())
        for offset in range(0,len(b),REPLY_CHUNK):self.send('chunk',offset=offset,data=base64.b64encode(b[offset:offset+REPLY_CHUNK]).decode())
    def start(self):
        self.prepare();self.send('commit')
        if self.reply.pending_pause:self.reply.notify('Player.OnPause')
        self.reply.tick(True)
        self.reply.events.put_nowait((self.reply.player_token,{'event':'started'}));self.reply.tick(True)
    def finish(self):
        self.reply.events.put_nowait((self.reply.player_token,{'event':'finished','ok':True}));self.reply.tick(True)
    def test_clean_owned_pause_and_finished_release_resume_once(self):
        self.start();self.assertEqual(self.reply.status['state'],'playing');self.assertTrue(self.reply.status['paused_by_us'])
        self.finish();self.assertEqual(self.backend.calls,['pause','suspend','launch','restore'])
        message=self.send('release',resume=True);self.assertEqual(self.backend.calls[-1],'resume')
        self.reply.receive(message,self.key);self.assertEqual(self.backend.calls.count('resume'),1)
        self.assertFalse(list(Path(self.tmp.name).glob('*.wav')))
    def test_prepaused_and_no_player_never_resume(self):
        for current in [replace(self.backend.current,playing=False),None]:
            self.backend.current=current;self.key=str(uuid4());self.ref=str(uuid4());self.seq=-1
            self.start();self.finish();self.send('release',resume=True)
        self.assertNotIn('pause',self.backend.calls);self.assertNotIn('resume',self.backend.calls)
    def test_seek_stop_resume_source_and_lost_client_forfeit(self):
        for method in ['Player.OnSeek','Player.OnStop','Player.OnResume','Player.OnAVStart',None]:
            self.key=str(uuid4());self.ref=str(uuid4());self.seq=-1;self.backend.current=PlaybackSnapshot('pi-video',str(self.reply.epoch),'same-item',True,25,0)
            self.start()
            if method:self.reply.notify(method)
            else:self.reply.tick(False)
            self.assertIn(self.reply.status['state'],{'interrupted','cancelled'})
            self.assertFalse(self.reply.suspended);self.assertNotIn('resume',self.backend.calls)
    def test_item_or_position_change_without_notification_cannot_resume(self):
        for change in [dict(item='new-item'),dict(position=45)]:
            self.key=str(uuid4());self.ref=str(uuid4());self.seq=-1;self.backend.current=PlaybackSnapshot('pi-video',str(self.reply.epoch),'same-item',True,25,0)
            self.start();self.finish();self.backend.current=replace(self.backend.current,**change);self.send('release',resume=True)
            self.assertEqual(self.reply.status['resume_result'],'forfeited')
        self.assertNotIn('resume',self.backend.calls)
    def test_timeout_before_pause_ack_and_late_old_completion(self):
        self.prepare();self.send('commit');self.reply.deadline=0;self.reply.tick(True)
        self.assertEqual(self.reply.status['state'],'failed');self.assertNotIn('launch',self.backend.calls)
        self.key=str(uuid4());self.ref=str(uuid4());self.seq=-1;self.backend.current=replace(self.backend.current,playing=True)
        self.start();old=self.reply.player_token;self.reply.abort();self.reply.events.put_nowait((old,{'event':'finished','ok':True}));self.reply.tick(True)
        self.assertEqual(self.reply.status['state'],'cancelled');self.assertNotIn('resume',self.backend.calls)
    def test_volume_and_mute_scale_pcm_without_changing_settings(self):
        for gain in [.5,0]:
            self.key=str(uuid4());self.ref=str(uuid4());self.seq=-1;self.backend.level=gain;self.backend.current=replace(self.backend.current,playing=False)
            self.prepare();self.send('commit')
            with wave.open(str(self.reply.path),'rb') as w:first=int.from_bytes(w.readframes(1),'little',signed=True)
            self.assertEqual(first,round(8192*gain));self.reply.abort()
    def test_prepare_chunk_ownership_digest_and_closed_protocol(self):
        self.prepare();m=self.send('commit');self.assertEqual(self.backend.calls,['pause'])
        self.reply.receive(m,'different-owner');self.assertEqual(self.backend.calls,['pause'])
        for change in [dict(url='http://not-accepted'),dict(command=None),dict(size=99_000_000)]:
            with self.assertRaises(ValueError):validate({**m,**change})
        now=__import__('time').time();b=wav()[:REPLY_CHUNK]
        m=dict(version=1,request_id=self.key,audio_ref=self.ref,sequence=10,issued_at=now,expires_at=now+7,command='reply_chunk',offset=0,data=base64.b64encode(b).decode())
        self.assertEqual(unseal(seal(m,b'1'*32),b'1'*32),m)
    def test_native_internal_player_metadata_and_external_player_refusal(self):
        b=module.KodiBackend()
        players=[{'playerid':1,'type':'video','playertype':'internal'}]
        def rpc(method,**params):
            if method=='Player.GetActivePlayers':return players
            if method=='Player.GetItem':return {'item':{'file':'fixture-local-media'}}
            return {'speed':0,'time':{'hours':0,'minutes':17,'seconds':5,'milliseconds':688}}
        b.rpc=rpc;self.assertFalse(b.snapshot(0,0).playing)
        players[0]['playertype']='external'
        with self.assertRaises(ValueError):b.snapshot(0,0)

    def test_missing_item_or_malformed_position_cannot_lend_the_video_sink(self):
        b=module.KodiBackend()
        for item,stamp in [(None,dict(hours=0,minutes=0,seconds=0,milliseconds=0)),
                           ('fixture',dict(hours=0,minutes=0,seconds=0,milliseconds=True)),
                           ('fixture',dict(hours=0,minutes=60,seconds=0,milliseconds=0)),('fixture',None)]:
            def rpc(method,**params):
                if method=='Player.GetActivePlayers':return [dict(playerid=1,type='video',playertype='internal')]
                if method=='Player.GetProperties':return dict(speed=1,time=stamp)
                if method=='Player.GetItem':return {'item':{'file':item}}
                raise AssertionError('No write allowed')
            b.rpc=rpc
            with self.subTest(item=item,stamp=stamp),self.assertRaises(ValueError):b.snapshot(0,0)

    def test_failed_helper_restores_engine_and_never_resumes(self):
        self.start();self.reply.events.put_nowait((self.reply.player_token,{'event':'finished','ok':False}));self.reply.tick(True)
        self.assertEqual(self.reply.status['state'],'failed');self.assertFalse(self.reply.suspended);self.assertNotIn('resume',self.backend.calls)

    def test_audio_race_after_preflight_rejects_exact_commit_without_handoff(self):
        # HA may have observed an eligible idle/video state before this upload.
        self.prepare()
        actual=module.KodiBackend();calls=[]
        def rpc(method,**params):
            calls.append(method)
            return [{'playerid':0,'type':'audio','playertype':'internal'}]
        actual.rpc=rpc;self.backend.snapshot=actual.snapshot
        message=self.send('commit')
        self.assertEqual(calls,['Player.GetActivePlayers']);self.assertEqual(self.backend.calls,[])
        from manual_protocol import reply_rejected_before_handoff
        self.assertTrue(reply_rejected_before_handoff(self.reply.status,self.key,self.ref,self.seq,self.reply.digest))
        self.assertFalse(list(Path(self.tmp.name).glob('reply-*.wav')))
        self.reply.receive(message,self.key)
        self.assertEqual(calls,['Player.GetActivePlayers']);self.assertEqual(self.backend.calls,[])

    def test_pause_or_sink_failure_after_possible_handoff_never_proves_rejection(self):
        from manual_protocol import reply_rejected_before_handoff
        for stage in ['pause','suspend']:
            self.key=str(uuid4());self.ref=str(uuid4());self.seq=-1
            self.backend=Backend(playing=stage=='pause')
            self.reply=module.ReplyAudio(self.backend,self.tmp.name);self.prepare()
            def fail():raise RuntimeError('uncertain native effect')
            setattr(self.backend,stage,fail)
            with self.assertRaises(RuntimeError):self.send('commit')
            self.reply.abort('failed')
            self.assertTrue(self.reply.status['handoff_attempted'])
            self.assertFalse(reply_rejected_before_handoff(self.reply.status,self.key,self.ref,self.seq,self.reply.digest))

if __name__=='__main__':unittest.main()
