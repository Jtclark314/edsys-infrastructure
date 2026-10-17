import base64
import copy
import importlib.util
from pathlib import Path
import unittest
import json
import time
from uuid import uuid4

spec=importlib.util.spec_from_file_location('manual_protocol',Path(__file__).resolve().parents[1]/'manual_protocol.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


class ManualProtocolTests(unittest.TestCase):
    def message(self):
        return dict(version=1,request_id=str(uuid4()),sequence=0,issued_at=1000,expires_at=1030,command='show',kind='butler',heading='Hello',body='Bedroom screen',rows=[],timezone='America/New_York')

    def test_executable_url_audio_unknown_fields_and_stale_messages_are_rejected(self):
        for change in [{'url':'http://example.invalid'},{'command':'execute'},{'audio':'start'},{'expires_at':999},{'issued_at':1050},{'sequence':True},{'body':'x'*1201}]:
            m=self.message();m.update(change)
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):p.validate(m,1000)

    def test_replacement_dismissal_duplicates_and_old_frames_cannot_reappear(self):
        guard=p.SequenceGuard();first=self.message();second=self.message()
        self.assertTrue(guard.accept(first,1000));self.assertFalse(guard.accept(first,1000))
        self.assertTrue(guard.accept(second,1000));first['sequence']=1
        self.assertFalse(guard.accept(first,1000))
        guard.retire();second['sequence']=1
        self.assertFalse(guard.accept(second,1000))

    def test_camera_accepts_only_bounded_jpeg_and_declared_sizes(self):
        m=self.message()
        for key in ['kind','heading','body','rows','timezone']:m.pop(key)
        m.update(command='camera_frame',label='Fixture camera',mode='popup',jpeg=base64.b64encode(b'\xff\xd8'+b'x'*100+b'\xff\xd9').decode())
        p.validate(m,1000)
        for field,value in [('jpeg',base64.b64encode(b'not an image').decode()),('mode','executable'),('jpeg','x'*(p.MAX_JPEG*2))]:
            candidate=copy.deepcopy(m);candidate[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):p.validate(candidate,1000)

    def test_private_content_is_encrypted_tamper_rejected_and_rpc_fits_64k(self):
        m=self.message();m.update(issued_at=time.time(),expires_at=time.time()+30)
        m['body']='private household fixture';key=b'k'*32
        wire=p.seal(m,key)
        self.assertNotIn(m['body'],json.dumps(wire));self.assertEqual(p.unseal(wire,key),m)
        with self.assertRaises(ValueError):p.unseal(wire,b'x'*32)
        for name in ['kind','heading','body','rows','timezone']:m.pop(name)
        m.update(command='camera_frame',label='Fixture',mode='popup',jpeg=base64.b64encode(b'\xff\xd8'+b'x'*(p.MAX_JPEG-4)+b'\xff\xd9').decode())
        body=json.dumps({'jsonrpc':'2.0','id':1,'method':'JSONRPC.NotifyAll','params':{'sender':p.SENDER,'message':p.MESSAGE,'data':p.seal(m,key)}})
        self.assertLess(len(body.encode()),65536)

    def test_long_art_lifetime_is_not_granted_to_other_views_or_arbitrary_assets(self):
        m=self.message();m.update(kind='art',art_index=2,expires_at=8200)
        p.validate(m,1000)
        for change in [{'kind':'butler'},{'art_index':-1},{'art_index':True},{'art_index':4},{'expires_at':8202},{'path':'/private/photo.jpg'},{'url':'https://example.invalid'}]:
            c={**m,**change}
            with self.subTest(change=change),self.assertRaises(ValueError):p.validate(c,1000)

    def test_reply_route_accepts_only_idle_or_explicit_internal_video_normal_speed(self):
        video={'playerid':1,'type':'video','playertype':'internal'}
        self.assertIsNone(p.reply_player_id([]));self.assertEqual(p.reply_player_id([video]),1)
        for players in [None,{},[video,video],[None],[{**video,'playerid':True}],
                        [{**video,'playerid':0,'type':'audio'}],[{**video,'playertype':'external'}],
                        [{'playerid':1,'type':'video'}]]:
            with self.subTest(players=players),self.assertRaises(ValueError):p.reply_player_id(players)
        for speed in [0,1]:self.assertEqual(p.reply_video_playing({'speed':speed}),speed==1)
        for properties in [None,{},dict(speed=True),dict(speed=2),dict(speed=1.0)]:
            with self.subTest(properties=properties),self.assertRaises(ValueError):p.reply_video_playing(properties)

    def test_only_exact_unused_commit_rejection_is_fallback_proof(self):
        key,ref=str(uuid4()),str(uuid4());digest='a'*64
        status=dict(version=1,request_id=key,audio_ref=ref,sequence=4,sha256=digest,
                    state='rejected',reason='player_state_unsupported',started=False,
                    paused_by_us=False,handoff_attempted=False)
        self.assertTrue(p.reply_rejected_before_handoff(status,key,ref,4,digest))
        for change in [dict(version=True),dict(request_id=str(uuid4())),dict(audio_ref=str(uuid4())),dict(sequence=3),
                       dict(sequence=True),dict(sha256='b'*64),dict(state='failed'),dict(reason='timeout'),
                       dict(started=True),dict(started=0),dict(paused_by_us=True),dict(handoff_attempted=True),
                       dict(handoff_attempted=None)]:
            with self.subTest(change=change):self.assertFalse(p.reply_rejected_before_handoff({**status,**change},key,ref,4,digest))
        self.assertFalse(p.reply_rejected_before_handoff(status,key,ref,4,None))

if __name__=='__main__':unittest.main()
