"""Core-native media authorization/routing checks; all device services are fake."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from homeassistant.core import Context, State
from homeassistant.components import conversation
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.helpers import intent
from custom_components.edsys_voice_knowledge.media_remote import MediaRemote, ControlMediaRemote
from custom_components.edsys_voice_knowledge.pilot import Request, CURRENT_REQUEST
from custom_components.edsys_voice_knowledge.pilot_policy import PilotRefusal
from custom_components.edsys_voice_knowledge.conversation import EdSysVoice
import native_pilot_checks as pilot_checks


class NativeMediaChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUpFixture=pilot_checks.NativePilotChecks.asyncSetUp
    asyncTearDown=pilot_checks.NativePilotChecks.asyncTearDown

    async def asyncSetUp(self):
        await self.asyncSetUpFixture()
        config={'targets':{role:{'entity_id':eid,'device_id':'fixture','config_entry_id':'fixture','name':role}
                           for role,eid in [('tv','media_player.tv_fixture'),('pi','media_player.pi_fixture')]},'pi_sources':['PC']}
        self.remote=MediaRemote(self.hass,self.pilot,config)
        self.states['media_player.tv_fixture']=State('media_player.tv_fixture','on',{
            'supported_features':int(F.TURN_ON|F.TURN_OFF|F.VOLUME_SET|F.VOLUME_MUTE|F.VOLUME_STEP|F.SELECT_SOURCE|F.PLAY|F.PAUSE|F.STOP),
            'source':'PC','app_id':'com.webos.app.hdmi1','source_list':['PC','Plex','Netflix'],'volume_level':.25,'is_volume_muted':False})
        self.states['media_player.pi_fixture']=State('media_player.pi_fixture','playing',{
            'supported_features':int(F.PLAY|F.PAUSE|F.STOP|F.SEEK|F.PREVIOUS_TRACK|F.NEXT_TRACK),'media_position':120,'media_duration':3600})
        self.hass.data={'edsys_voice_knowledge':{'pilot':self.pilot,'media_remote':self.remote}}
        self.hass.config=SimpleNamespace(language='en')
        self.original_call=self.hass.services.async_call
        async def call(domain,service,data,**kwargs):
            await self.original_call(domain,service,data,**kwargs)
            eid=data['entity_id'];s=self.states[eid];a=dict(s.attributes)
            if service=='volume_up':a['volume_level']+=.01
            if service=='volume_down':a['volume_level']-=.01
            if service=='media_seek':a['media_position']=data['seek_position']
            if service=='command' and data.get('command')=='tv/switchInput':a['app_id']='com.webos.app.hdmi'+data['payload']['inputId'][-1]
            self.states[eid]=State(eid,s.state,a)
        self.hass.services.async_call=call

    def request(self,text):return Request(text,Context(user_id='fixture'),'nimo_fixture')

    async def test_pause_uses_visible_pi_and_lg_override_uses_tv(self):
        result=await self.remote.control(self.request('Pause Plex'),'active','pause')
        self.assertTrue(result['verified']);self.assertEqual(self.calls[0][2]['entity_id'],'media_player.pi_fixture')
        result=await self.remote.control(self.request('Pause the LG'),'tv','pause')
        self.assertEqual(result['status'],'submitted');self.assertEqual(self.calls[-1][2]['entity_id'],'media_player.tv_fixture')

    async def test_native_app_id_overrides_stale_pc_source_label(self):
        s=self.states['media_player.tv_fixture'];a=dict(s.attributes);a['app_id']='netflix'
        self.assertEqual(self.remote.selected(State(s.entity_id,'on',a)),'tv')

    async def test_volume_always_targets_lg_and_readback_is_verified(self):
        result=await self.remote.control(self.request('Could you lower the sound just a little on the Pi?'),'pi','volume_down')
        self.assertTrue(result['verified']);self.assertEqual(self.calls[0][2]['entity_id'],'media_player.tv_fixture')
        self.assertEqual(self.states['media_player.tv_fixture'].attributes['volume_level'],.24)

    async def test_exact_volume_applies_a_one_percentage_point_difference(self):
        result=await self.remote.control(self.request('Set TV volume to 26 percent'),'active','set_volume',26)
        self.assertTrue(result['verified'])
        self.assertEqual(self.states['media_player.tv_fixture'].attributes['volume_level'],.26)
        self.assertEqual(self.calls[0][1],'volume_set')

    async def test_pause_sends_explicit_state_even_when_polled_snapshot_says_paused(self):
        s=self.states['media_player.pi_fixture'];self.states[s.entity_id]=State(s.entity_id,'paused',s.attributes)
        result=await self.remote.control(self.request('Pause Pi'),'pi','pause')
        self.assertTrue(result['verified'])
        self.assertEqual(self.calls[0][1],'media_pause')
        self.assertEqual(self.calls[0][2]['entity_id'],s.entity_id)

    async def test_navigation_is_acknowledged_not_reported_selected(self):
        result=await self.remote.control(self.request('Press right on Pi'),'active','right')
        self.assertEqual(result['status'],'submitted');self.assertFalse(result['verified']);self.assertIn('Sent',result['message'])
        self.assertEqual(self.calls[0][:2],('kodi','call_method'));self.assertEqual(self.calls[0][2]['method'],'Input.Right')

    async def test_only_one_action_per_authenticated_turn_even_if_model_changes_operation(self):
        request=self.request('Press right on Pi')
        first=await self.remote.control(request,'pi','right')
        self.assertEqual(await self.remote.control(request,'active','right'),first)
        self.assertEqual(len(self.calls),1)
        # A second instance with the same HA context is also deduplicated.
        self.assertEqual(await self.remote.control(Request(request.text,request.context,None),'pi','right'),first)
        self.assertEqual(len(self.calls),1)

    async def test_timeout_does_not_replay_non_idempotent_key(self):
        self.timeout=True;request=self.request('Press right on Pi')
        for _ in range(2):self.assertEqual((await self.remote.control(request,'pi','right'))['status'],'uncertain')
        self.assertEqual(len(self.calls),1)

    async def test_identity_permission_exposure_room_and_unavailable_block_dispatch(self):
        for scenario in ['identity','permission','exposure','room','unavailable']:
            self.registry.config_entry_id='fixture';self.authorized=self.exposed=True
            self.remote.config['targets']['pi'].pop('area_id',None)
            if scenario=='identity':self.registry.config_entry_id='changed'
            if scenario=='permission':self.authorized=False
            if scenario=='exposure':self.exposed=False
            if scenario=='room':self.remote.config['targets']['pi']['area_id']='changed'
            if scenario=='unavailable':self.states['media_player.pi_fixture']=State('media_player.pi_fixture','unavailable')
            with self.subTest(scenario=scenario),patch('custom_components.edsys_voice_knowledge.pilot.er.async_get_effective_area_id',return_value=None),self.assertRaises(PilotRefusal):
                await self.remote.control(self.request('Press right on Pi'),'pi','right')
        self.assertEqual(self.calls,[])

    async def test_inactive_pi_and_mismatched_explicit_target_are_refused(self):
        with self.assertRaises(PilotRefusal):await self.remote.control(self.request('Press right on Pi'),'tv','right')
        self.states['media_player.pi_fixture']=State('media_player.pi_fixture','idle')
        with self.assertRaises(PilotRefusal):await self.remote.control(self.request('Pause Pi'),'pi','pause')
        self.assertEqual(self.calls,[])

    async def test_source_must_exist_and_seek_must_be_in_current_media(self):
        with self.assertRaises(PilotRefusal):await self.remote.control(self.request('Open Missing on LG'),'tv','select_source',source='Missing')
        with self.assertRaises(PilotRefusal):await self.remote.control(self.request('Jump to 4000 seconds on Pi'),'pi','seek',4000)
        self.assertEqual(self.calls,[])
        result=await self.remote.control(self.request('Skip forward 30 seconds on Pi'),'pi','seek_relative',30)
        self.assertTrue(result['verified']);self.assertEqual(next(c[2]['seek_position'] for c in self.calls if c[1]=='media_seek'),150)

    async def test_channel_sequence_requires_live_tv_and_is_bounded(self):
        with self.assertRaises(PilotRefusal):await self.remote.control(self.request('Change TV to channel 42'),'tv','channel',42)
        s=self.states['media_player.tv_fixture'];self.states[s.entity_id]=State(s.entity_id,'on',{**s.attributes,'source':'Live TV','app_id':'live'})
        result=await self.remote.control(self.request('Change TV to channel 42'),'tv','channel',42)
        self.assertEqual(result['status'],'submitted');self.assertEqual([c[2]['button'] for c in self.calls],['4','2','ENTER'])

    async def test_wake_failure_and_mapping_change_preserve_uncertainty(self):
        s=self.states['media_player.tv_fixture'];self.states[s.entity_id]=State(s.entity_id,'off',s.attributes)
        async def wake_then_drift(*args,**kwargs):
            await self.original_call(*args,**kwargs);self.registry.config_entry_id='changed'
        self.hass.services.async_call=wake_then_drift;request=self.request('Open Plex on LG')
        result=await self.remote.control(request,'tv','open_plex')
        self.assertEqual(result['status'],'uncertain');self.assertEqual(len(self.calls),1)
        self.assertEqual((await self.remote.control(request,'tv','open_plex'))['status'],'uncertain');self.assertEqual(len(self.calls),1)

    async def test_fast_path_runs_without_cloud_but_paraphrase_delegates(self):
        self.assertTrue((await self.remote.local(self.request('Pause Plex'),{'match':False}))['verified'])
        self.assertEqual(await self.remote.local(self.request('Could you lower the sound just a little on the TV?'),{'match':False}),{'delegate_media':True})
        self.assertIsNone(await self.remote.local(self.request('Turn on kitchen lights'),{'match':True,'targets':{'light.other':{'matched':True}}}))

    async def test_cloud_tool_requires_authenticated_request_context(self):
        tool=ControlMediaRemote();context=Context(user_id='fixture')
        result=await tool.async_call(self.hass,SimpleNamespace(tool_args={}),SimpleNamespace(context=context))
        self.assertTrue(result.error);self.assertEqual(self.calls,[])

    async def test_cloud_api_publishes_new_media_tools_and_light_only_legacy_control(self):
        from custom_components.edsys_voice_knowledge import KnowledgeAPI
        api=KnowledgeAPI(hass=self.hass,id='fixture',name='Fixture')
        instance=await api.async_get_api_instance(SimpleNamespace(context=self.context))
        tools={tool.name:tool for tool in instance.tools}
        self.assertIn('lookup_bedroom_media',tools);self.assertIn('control_bedroom_media',tools)
        args={'target':'pi','operation':'pause','value':None,'source':None}
        self.assertEqual(tools['control_bedroom_media'].parameters(args),args)
        with self.assertRaises(Exception):tools['control_home_pilot'].parameters({'target':'master_bedroom_tv','operation':'off','value':None})
        self.assertNotIn('supersedes',instance.api_prompt)

    async def test_inactive_visible_screen_requires_wake_but_explicit_pi_is_allowed(self):
        s=self.states['media_player.tv_fixture'];self.states[s.entity_id]=State(s.entity_id,'off',s.attributes)
        with self.assertRaises(PilotRefusal):await self.remote.control(self.request('Pause Plex'),'active','pause')
        self.assertEqual(self.calls,[])
        self.assertTrue((await self.remote.control(self.request('Pause Pi'),'pi','pause'))['verified'])

    async def test_cloud_paraphrase_uses_same_executor_and_model_cannot_claim_button_success(self):
        self.states['conversation.backend']=State('conversation.backend','ready')
        native=SimpleNamespace(async_debug_recognize=AsyncMock(return_value={'match':False}))
        entity=EdSysVoice(SimpleNamespace(entry_id='fixture'),'conversation.backend');entity.hass=self.hass
        request=self.request('Could you move right on the Pi remote?')
        user_input=conversation.ConversationInput(text=request.text,context=request.context,conversation_id=None,device_id='nimo_fixture',satellite_id=None,language='en',agent_id='conversation.fixture')
        async def backend(**kwargs):
            result=await ControlMediaRemote().async_call(self.hass,SimpleNamespace(tool_args={'target':'pi','operation':'right','value':None,'source':None}),SimpleNamespace(context=request.context))
            self.assertFalse(result.error)
            response=intent.IntentResponse(language='en');response.async_set_speech('Done, selected the right item.')
            return conversation.ConversationResult(response=response,conversation_id=None)
        # Force a cloud phrase: direct parser normally recognizes "move right".
        with patch.object(self.remote,'local',AsyncMock(return_value={'delegate_media':True})),patch('homeassistant.components.conversation.async_get_agent',return_value=native),patch('homeassistant.components.conversation.async_converse',side_effect=backend):
            result=await entity.async_process(user_input)
        self.assertIn('Sent right',result.response.speech['plain']['speech']);self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
