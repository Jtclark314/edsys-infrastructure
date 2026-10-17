"""Actual HA routing/relay classes with local transport/Core stubs only.

These checks use no installed Core runtime, network, devices or provider API.
Native Core/frontend qualification remains a separate held acceptance gate.
"""
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import wave

ROOT=Path(__file__).resolve().parents[1]
PI=ROOT.parent/'pi5-bedroom-display'
PACKAGE='a1_voice_fixture'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module);return module


def pcm():
    b=io.BytesIO()
    with wave.open(b,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(bytes(48000))
    return b.getvalue()


class RoutingChecks(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules=patch.dict(sys.modules);cls.modules.start()
        package=ModuleType(PACKAGE);package.__path__=[];sys.modules[PACKAGE]=package
        core=ModuleType('homeassistant.core')
        core.Context=lambda **kw:SimpleNamespace(**kw)
        ws=SimpleNamespace(websocket_command=lambda _:lambda fn:fn,async_response=lambda fn:fn)
        async def native(*args):pass
        native._ws_schema=lambda msg:msg;native.__wrapped__=native
        fake={
            'probatio':SimpleNamespace(**{k:(lambda *a,**kw:None) for k in ('All','Schema','Required','Optional','Any','Range','In')},ALLOW_EXTRA=object(),Invalid=ValueError),
            'homeassistant':ModuleType('homeassistant'),
            'homeassistant.core':core,
            'homeassistant.components':SimpleNamespace(websocket_api=ws,tts=SimpleNamespace(async_get_stream=Mock())),
            'homeassistant.components.assist_pipeline':ModuleType('assist_pipeline'),
            'homeassistant.components.assist_pipeline.pipeline':SimpleNamespace(async_get_pipeline=Mock()),
            'homeassistant.components.assist_pipeline.websocket_api':SimpleNamespace(websocket_run=native),
            PACKAGE+'.bedroom_screen':SimpleNamespace(ERRORS=(TimeoutError,OSError,ValueError,RuntimeError)),
            PACKAGE+'.pilot_policy':SimpleNamespace(PilotRefusal=ValueError),
        }
        sys.modules.update(fake);package.DOMAIN='edsys_voice_knowledge'
        cls.protocol=load(PACKAGE+'.screen_protocol',PI/'manual_protocol.py')
        folder=ROOT/'custom_components/edsys_voice_knowledge'
        cls.reply=load(PACKAGE+'.phil_reply',folder/'phil_reply.py')
        cls.voice_module=load(PACKAGE+'.phil_voice',folder/'phil_voice.py')

    @classmethod
    def tearDownClass(cls):cls.modules.stop()

    async def asyncSetUp(self):
        self.players=[];self.speed=1;self.methods=[];self.messages=[]
        self.authorized=True;self.voice_status={};self.reply_status={};self.commit_result='rejected'
        self.pipeline_calls=0
        async def executor(fn,*args):return fn(*args)
        self.screen=SimpleNamespace(
            authorize=self.authorize,rpc=self.rpc,send=self.send,
            envelope=lambda key,command,expires,sequence=0,**data:dict(request_id=key,command=command,sequence=sequence,**data),
            hass=SimpleNamespace(async_add_executor_job=executor),pilot=SimpleNamespace(nimo_device_id='registered-fixture'))
        self.voice=self.voice_module.PhilVoice(self.screen,'named-fixture','pi')

    async def authorize(self,context):
        if not self.authorized:raise ValueError('Caller/TV/input no longer authorized')

    async def rpc(self,method,**params):
        self.methods.append((method,params))
        if method=='Player.GetActivePlayers':return self.players
        if method=='Player.GetProperties':return {'speed':self.speed}
        if method=='XBMC.GetInfoLabels':
            label=params['labels'][0]
            status=self.voice_status if label==self.protocol.VOICE_INFO_LABEL else self.reply_status
            return {label:json.dumps(status)}
        raise AssertionError('Unexpected RPC')

    async def send(self,message):
        self.messages.append(message)
        command=message['command']
        if command=='voice':
            self.voice_status=dict(version=1,request_id=message['request_id'],sequence=message['sequence'],
                                   state=message['state'],visible=message['state'] not in {'idle','cancelled'})
            return
        self.reply_status.update(version=1,request_id=message['request_id'],audio_ref=message['audio_ref'],sequence=message['sequence'])
        if command=='reply_prepare':
            self.reply_status.update(state='receiving',received=0,sha256=message['sha256'],started=False,paused_by_us=False,handoff_attempted=False)
        elif command=='reply_chunk':self.reply_status['received']+=len(base64.b64decode(message['data']))
        elif command=='reply_commit':
            self.reply_status.update(state='rejected',reason='player_state_unsupported')
            if self.commit_result=='lost_ack':raise TimeoutError
            if self.commit_result=='malformed':self.reply_status.pop('handoff_attempted')
            if self.commit_result=='handoff':self.reply_status['handoff_attempted']=True
            if self.commit_result=='started':self.reply_status['started']=True
            if self.commit_result=='wrong_digest':self.reply_status['sha256']='0'*64
            if self.commit_result=='wrong_sequence':self.reply_status['sequence']-=1
            if self.commit_result=='cancelled':await self.voice.finish()

    async def begin(self):return await self.voice.begin('owner-fixture','named-fixture')

    async def test_idle_video_and_prepaused_select_pi_without_title_or_url_reads(self):
        for players,speed in [([],1),([dict(playerid=1,type='video',playertype='internal')],1),
                              ([dict(playerid=1,type='video',playertype='internal')],0)]:
            self.players,self.speed=players,speed
            result=await self.begin();self.assertEqual(result['output'],'pi')
            self.assertEqual(self.voice.active['output'],'pi')
        allowed={'Player.GetActivePlayers','Player.GetProperties','XBMC.GetInfoLabels'}
        self.assertTrue(all(method in allowed for method,_ in self.methods))
        self.assertTrue(all(params['properties']==['speed'] for method,params in self.methods if method=='Player.GetProperties'))
        self.assertFalse(any(m['command'].startswith('reply_') for m in self.messages))

    async def test_audio_external_multiple_missing_metadata_and_unknown_select_nimo_animation(self):
        video=dict(playerid=1,type='video',playertype='internal')
        for players,speed in [([dict(playerid=0,type='audio',playertype='internal')],1),
                              ([{**video,'playertype':'external'}],1),([video,video],1),
                              ([dict(playerid=1,type='video')],1),(None,1),([video],2),([video],True)]:
            self.players,self.speed=players,speed
            with self.subTest(players=players,speed=speed):
                result=await self.begin();self.assertTrue(result['active']);self.assertEqual(result['output'],'nimo')
                self.assertEqual(self.messages[-1]['output'],'nimo');self.assertFalse(self.messages[-1]['audio_playing'])
        self.assertFalse(any(m['command'].startswith('reply_') for m in self.messages))

    async def test_unavailable_native_handler_or_player_read_retains_nimo_output(self):
        with patch.object(self.reply,'native_ready',return_value=False):
            self.assertEqual((await self.begin())['output'],'nimo')
        original=self.screen.rpc
        async def failed(method,**params):
            if method=='Player.GetActivePlayers':raise TimeoutError
            return await original(method,**params)
        self.screen.rpc=failed
        self.assertEqual((await self.begin())['output'],'nimo')

    async def test_authorization_change_during_preflight_cannot_show_any_new_lease(self):
        original=self.screen.rpc
        async def changed(method,**params):
            result=await original(method,**params)
            if method=='Player.GetActivePlayers':self.authorized=False
            return result
        self.screen.rpc=changed
        self.assertFalse((await self.begin())['active']);self.assertEqual(self.messages,[])
        self.assertIsNone(self.voice.active)

    async def relay(self,mode):
        self.commit_result=mode
        await self.begin();lease=self.voice.active
        lease.update(tts_engine='tts-fixture',stream_token='native-fixture')
        async def generated():yield pcm()
        async def native_pipeline(*args):self.pipeline_calls+=1
        self.screen.hass.data={'edsys_voice_knowledge':{'phil_voice':self.voice}}
        connection=SimpleNamespace(user=SimpleNamespace(id='owner-fixture'),send_error=Mock())
        message=dict(id=1,type='edsys_voice_knowledge/phil_pipeline',phil_session_id=lease['id'],pipeline='named-fixture',
                     device_id='registered-fixture',start_stage='stt',end_stage='tts')
        with patch.object(self.reply.native_run,'__wrapped__',native_pipeline),patch.object(self.reply,'async_get_pipeline',return_value=SimpleNamespace(tts_engine='tts-fixture')):
            await self.reply.websocket_pipeline(self.screen.hass,connection,message)
            # A duplicate client subscription must not submit a second command.
            await self.reply.websocket_pipeline(self.screen.hass,connection,message)
        self.assertEqual(self.pipeline_calls,1)
        with patch.object(self.reply.tts,'async_get_stream',return_value=SimpleNamespace(engine='tts-fixture',async_stream_result=generated)),patch.object(self.reply,'pcm_wave',side_effect=lambda data:data):
            if mode=='wrong_sequence':
                # A wrong acknowledgement must time out, never become proof.
                original=self.voice.replies.read
                async def read(lease):
                    result=await original(lease)
                    if result and result.get('state')=='rejected' and result.get('sequence')!=lease['reply_sequence']:raise TimeoutError
                    return result
                self.voice.replies.read=read
            await self.voice.replies.relay(lease)
        return lease,self.voice.status('owner-fixture',lease['id'])

    async def test_audio_start_race_gets_same_reply_fallback_only_after_exact_unused_commit_rejection(self):
        lease,status=await self.relay('rejected')
        self.assertTrue(lease['commit_attempted']);self.assertFalse(status['active'])
        self.assertEqual(status['audio_state'],'failed');self.assertTrue(status['fallback_allowed'])
        self.assertFalse(status['started']);self.assertEqual(self.pipeline_calls,1)
        self.assertEqual(sum(m['command']=='reply_commit' for m in self.messages),1)
        self.assertFalse(any(m['command']=='reply_release' for m in self.messages))
        self.assertFalse(self.voice.status('another-user',lease['id'])['fallback_allowed'])

    async def test_lost_malformed_wrong_digest_sequence_or_post_handoff_ack_never_allows_fallback(self):
        for mode in ['lost_ack','malformed','handoff','started','wrong_digest','wrong_sequence']:
            await self.asyncSetUp()
            with self.subTest(mode=mode):
                lease,status=await self.relay(mode)
                self.assertEqual(status['audio_state'],'uncertain');self.assertFalse(status['fallback_allowed'])
                self.assertEqual(self.pipeline_calls,1)
                self.assertEqual(sum(m['command']=='reply_commit' for m in self.messages),1)

    async def test_cancelled_or_replaced_turn_cannot_acquire_late_fallback(self):
        lease,status=await self.relay('cancelled')
        self.assertFalse(status['fallback_allowed']);self.assertEqual(status['audio_state'],'cancelled')
        await self.begin();new=self.voice.active
        self.assertNotEqual(new['id'],lease['id']);self.assertFalse(self.voice.status('owner-fixture',lease['id'])['fallback_allowed'])


if __name__=='__main__':unittest.main()
