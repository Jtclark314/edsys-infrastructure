"""Phil lifecycle ordering and HA authorization; no audio or device actuation."""
import json
import time
import unittest
from unittest.mock import patch

from homeassistant.core import State
from custom_components.edsys_voice_knowledge.phil_voice import PhilVoice
from custom_components.edsys_voice_knowledge.pilot_policy import PilotRefusal
from custom_components.edsys_voice_knowledge.screen_protocol import VOICE_INFO_LABEL,unseal
from native_screen_checks import NativeScreenChecks


class NativePhilChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUpFixture=NativeScreenChecks.asyncSetUpFixture
    asyncTearDownFixture=NativeScreenChecks.asyncTearDownFixture
    asyncTearDown=NativeScreenChecks.asyncTearDown

    async def asyncSetUp(self):
        await NativeScreenChecks.asyncSetUp(self)
        self.voice_status={'version':1,'visible':False,'state':'idle'};self.messages=[]
        async def rpc(method,**data):
            if method=='Player.GetActivePlayers':return []
            if method=='XBMC.GetInfoLabels':return {VOICE_INFO_LABEL:json.dumps(self.voice_status)}
            m=unseal(data['data'],self.screen.key);self.messages.append(m)
            self.voice_status={'version':1,'request_id':m['request_id'],'sequence':m['sequence'],
                               'state':m['state'],'visible':m['state'] not in ('idle','cancelled')}
            return 'OK'
        self.screen.rpc=rpc;self.voice=PhilVoice(self.screen,'pipeline-fixture')

    async def begin(self):return await self.voice.begin('fixture','pipeline-fixture')

    async def send(self,lease,phase,sequence=0,ref=None,user='fixture'):
        return await self.voice.update(user,lease['session_id'],sequence,phase,ref)

    async def test_begin_is_visible_listening_without_media_or_audio_actions(self):
        lease=await self.begin();self.assertTrue(lease['active'])
        self.assertEqual(self.messages[-1]['state'],'listening');self.assertFalse(self.messages[-1]['audio_playing'])
        self.assertEqual(self.calls,[]);self.assertNotIn('text',self.messages[-1]);self.assertNotIn('url',self.messages[-1])

    async def test_speaking_requires_matched_audio_and_end_does_not_replay(self):
        lease=await self.begin();await self.send(lease,'processing')
        self.assertFalse(self.messages[-1]['audio_playing'])
        with self.assertRaises(PilotRefusal):await self.send(lease,'audio_start',1,'wrong')
        await self.send(lease,'audio_start',1,lease['audio_ref'])
        self.assertTrue(self.messages[-1]['audio_playing'])
        await self.send(lease,'processing',2);self.assertTrue(self.messages[-1]['audio_playing'])
        await self.send(lease,'audio_waiting',3,lease['audio_ref']);self.assertFalse(self.messages[-1]['audio_playing'])
        await self.send(lease,'audio_start',4,lease['audio_ref'])
        self.assertFalse((await self.send(lease,'audio_end',5,lease['audio_ref']))['active'])
        count=len(self.messages);self.assertFalse((await self.send(lease,'audio_start',6,lease['audio_ref']))['active'])
        self.assertEqual(len(self.messages),count);self.assertEqual(self.calls,[])

    async def test_other_user_wrong_pipeline_duplicate_and_prior_turn_are_rejected(self):
        self.assertFalse((await self.voice.begin('fixture','other'))['active'])
        first=await self.begin();self.assertFalse((await self.send(first,'processing',user='other'))['active'])
        await self.send(first,'processing')
        with self.assertRaises(PilotRefusal):await self.send(first,'processing')
        second=await self.begin();count=len(self.messages)
        self.assertFalse((await self.send(first,'failed',1))['active']);self.assertEqual(len(self.messages),count)
        self.assertEqual(self.voice.active['id'],second['session_id'])

    async def test_permissions_exposure_mapping_off_and_wrong_input_never_show(self):
        for scenario in ['permission','exposure','mapping','off','input']:
            self.authorized=self.exposed=True;self.registry.config_entry_id='fixture'
            s=self.states['media_player.tv_fixture'];attrs={**s.attributes,'app_id':'com.webos.app.hdmi1'}
            self.states[s.entity_id]=State(s.entity_id,'on',attrs)
            if scenario=='permission':self.authorized=False
            if scenario=='exposure':self.exposed=False
            if scenario=='mapping':self.registry.config_entry_id='changed'
            if scenario=='off':self.states[s.entity_id]=State(s.entity_id,'off',attrs)
            if scenario=='input':self.states[s.entity_id]=State(s.entity_id,'on',{**attrs,'app_id':'netflix'})
            with self.subTest(scenario=scenario):self.assertFalse((await self.begin())['active'])
        self.assertEqual(self.messages,[]);self.assertEqual(self.calls,[])

    async def test_tv_source_change_clears_owned_view_and_cannot_resume(self):
        lease=await self.begin();s=self.states['media_player.tv_fixture']
        self.states[s.entity_id]=State(s.entity_id,'on',{**s.attributes,'app_id':'netflix'})
        self.assertFalse((await self.send(lease,'heartbeat'))['active']);self.assertEqual(self.messages[-1]['state'],'cancelled')
        self.assertEqual(self.calls,[])

    async def test_lost_heartbeat_and_local_dismiss_forfeit_current_turn(self):
        lease=await self.begin();self.voice.active['seen']=time.monotonic()-7
        self.assertFalse((await self.send(lease,'heartbeat'))['active'])
        lease=await self.begin();self.voice_status.update(visible=False,state='dismissed')
        count=len(self.messages);self.assertFalse((await self.send(lease,'heartbeat'))['active'])
        self.assertEqual(len(self.messages),count+1);self.assertEqual(self.messages[-1]['state'],'cancelled')

    async def test_transport_uncertainty_never_retries_and_native_voice_can_continue(self):
        count=0
        async def failed(*a,**kw):
            nonlocal count;count+=1;raise TimeoutError
        self.screen.rpc=failed
        self.assertFalse((await self.begin())['active']);self.assertEqual(count,1)
