"""Run in an isolated Core Python process, with fakes that cannot reach devices."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from homeassistant.core import Context, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from custom_components.edsys_voice_knowledge.pilot import Pilot, Request
from custom_components.edsys_voice_knowledge.pilot_policy import PilotRefusal
from custom_components.edsys_voice_knowledge.conversation import EdSysVoice
from homeassistant.components import conversation
from homeassistant.helpers import intent
from unittest.mock import AsyncMock


class NativePilotChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.context = Context(user_id="fixture")
        self.bindings = {key: {"entity_id": eid, "device_id": "fixture", "config_entry_id": "fixture", "name": key}
                         for key,eid in [("gadget_lab_light","light.fixture"),("master_bedroom_tv","media_player.tv_fixture"),
                                         ("master_bedroom_plex","media_player.plex_fixture")]}
        self.cameras = {"front_porch": {"entity_id":"camera.fixture", "device_id":"fixture", "config_entry_id":"fixture",
                                        "name":"Front Porch", "aliases":["front porch"]}}
        self.states = {"light.fixture": State("light.fixture","off"), "camera.fixture": State("camera.fixture","recording"),
                       "media_player.tv_fixture": State("media_player.tv_fixture","on",{"supported_features":int(F.TURN_OFF|F.VOLUME_MUTE|F.VOLUME_SET|F.SELECT_SOURCE),"source":"Plex","source_list":["Plex"],"is_volume_muted":False}),
                       "media_player.plex_fixture": State("media_player.plex_fixture","playing",{"supported_features":int(F.PAUSE|F.PLAY|F.STOP)})}
        self.registry = SimpleNamespace(disabled_by=None,device_id="fixture",config_entry_id="fixture")
        self.authorized, self.exposed, self.timeout = True, True, False
        self.calls = []
        async def user(_):
            return SimpleNamespace(is_active=True,permissions=SimpleNamespace(check_entity=lambda *_:self.authorized))
        async def call(domain, service, data, **kwargs):
            self.calls.append((domain,service,data))
            if self.timeout:raise TimeoutError
            eid=data["entity_id"];old=self.states[eid];attributes=dict(old.attributes);state=old.state
            if service in ("turn_on","turn_off"):
                state=service.removeprefix("turn_")
                if eid=='media_player.tv_fixture' and service=='turn_on':attributes['source_list']=['Plex']
            if service=='select_source':attributes['source']=data['source']
            if service=="volume_mute":attributes["is_volume_muted"]=data["is_volume_muted"]
            if service=="volume_set":attributes["volume_level"]=data["volume_level"]
            if service=="media_pause":state="paused"
            if service=="media_play":state="playing"
            self.states[eid]=State(eid,state,attributes)
        self.hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=user),states=SimpleNamespace(get=self.states.get),
                                  services=SimpleNamespace(async_call=call))
        self.pilot=Pilot(self.hass,{"targets":self.bindings,"cameras":self.cameras},"nimo_fixture")
        self.patches=[patch("custom_components.edsys_voice_knowledge.pilot.er.async_get",return_value=SimpleNamespace(async_get=lambda _:self.registry)),
                      patch("homeassistant.components.homeassistant.exposed_entities.async_should_expose",side_effect=lambda *_:self.exposed)]
        for item in self.patches:item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):item.stop()

    def request(self,text,device="nimo_fixture"):
        return Request(text,self.context,device)

    async def test_exact_light_write_and_verified_result(self):
        result=await self.pilot.control(self.request("Turn on Gadget Lab lights"),"gadget_lab_light","on")
        self.assertTrue(result["verified"])
        self.assertEqual(self.calls[0][2],{"entity_id":"light.fixture"})

    async def test_unchanged_state_needs_no_repeat(self):
        self.states['light.fixture']=State('light.fixture','on')
        self.assertTrue((await self.pilot.control(self.request("Turn on Gadget Lab lights"),"gadget_lab_light","on"))["verified"])
        self.assertEqual(self.calls,[])

    async def test_timeout_is_not_replayed(self):
        self.timeout=True;request=self.request("Turn on Gadget Lab lights")
        for _ in range(2):self.assertFalse((await self.pilot.control(request,"gadget_lab_light","on"))["verified"])
        self.assertEqual(len(self.calls),1)

    async def test_permission_exposure_mapping_and_availability_fail_before_dispatch(self):
        scenarios=[("permission",False),("exposure",False),("mapping","changed"),("availability","unavailable")]
        for name,value in scenarios:
            self.context=Context(user_id="fixture")
            self.authorized=self.exposed=True;self.registry.config_entry_id="fixture";self.states['light.fixture']=State('light.fixture','off')
            if name=='permission':self.authorized=value
            if name=='exposure':self.exposed=value
            if name=='mapping':self.registry.config_entry_id=value
            if name=='availability':self.states['light.fixture']=State('light.fixture',value)
            with self.subTest(name=name),self.assertRaises(PilotRefusal):
                await self.pilot.control(self.request("Turn on Gadget Lab lights"),"gadget_lab_light","on")
        self.assertEqual(self.calls,[])

    async def test_unsupported_tv_wake_and_inactive_plex_fail_before_dispatch(self):
        with self.assertRaises(PilotRefusal):await self.pilot.control(self.request("Turn on the Master Bedroom TV"),"master_bedroom_tv","on")
        self.states['media_player.plex_fixture']=State('media_player.plex_fixture','idle',{'supported_features':int(F.PAUSE)})
        with self.assertRaises(PilotRefusal):await self.pilot.control(self.request("Pause Plex in the Master Bedroom"),"master_bedroom_plex","pause")
        self.assertEqual(self.calls,[])

    async def test_tv_transport_does_not_use_background_plex_session(self):
        self.states['media_player.tv_fixture']=State('media_player.tv_fixture','on',{'source':'Netflix'})
        with self.assertRaises(PilotRefusal):await self.pilot.control(self.request("Pause the Master Bedroom TV"),"master_bedroom_plex","pause")
        self.assertEqual(self.calls,[])

    async def test_missing_volume_readback_does_not_crash(self):
        result=await self.pilot.control(self.request("Set Master Bedroom TV volume to 25 percent"),"master_bedroom_tv","set_volume",.25)
        self.assertTrue(result['verified'])

    async def test_camera_presentation_requires_this_client_and_never_dispatches_service(self):
        with self.assertRaises(PilotRefusal):await self.pilot.camera(self.request("Show front porch camera",device="pi_fixture"))
        request=self.request("Show front porch camera");result=await self.pilot.camera(request)
        self.assertFalse(result['verified']);self.assertEqual(request.presentation['entity_id'],'camera.fixture')
        self.assertEqual(self.calls,[])

    async def test_native_recognition_uses_verified_pilot_executor(self):
        recognized={'intent':{'name':'HassTurnOn'},'targets':{'light.fixture':{'matched':True}}}
        result=await self.pilot.recognized_control(self.request('Turn on Gadget Lab lights'),recognized)
        self.assertTrue(result['verified']);self.assertEqual(len(self.calls),1)

    async def test_native_group_is_not_partially_executed(self):
        recognized={'intent':{'name':'HassTurnOff'},'targets':{'light.fixture':{'matched':True},'light.other':{'matched':True}}}
        with self.assertRaises(PilotRefusal):await self.pilot.recognized_control(self.request('Turn off Gadget Lab lights'),recognized)
        self.assertEqual(self.calls,[])

    async def test_native_nonpilot_command_is_not_taken_over(self):
        recognized={'intent':{'name':'HassTurnOn'},'targets':{'light.other':{'matched':True}}}
        self.assertIsNone(await self.pilot.recognized_control(self.request('Turn on kitchen lights'),recognized))
        self.assertEqual(self.calls,[])

    async def test_unsupported_native_pilot_action_cannot_bypass_verification(self):
        recognized={'intent':{'name':'HassLightSet'},'targets':{'light.fixture':{'matched':True}}}
        with self.assertRaises(PilotRefusal):
            await self.pilot.recognized_control(self.request('Set Gadget Lab lights to 30 percent'),recognized)
        self.assertEqual(self.calls,[])

    async def test_native_service_error_preserves_uncertainty_without_replay(self):
        self.hass.services.async_call=AsyncMock(side_effect=HomeAssistantError('fixture transport failure'))
        request=self.request('Turn on Gadget Lab lights')
        for _ in range(2):
            result=await self.pilot.control(request,'gadget_lab_light','on')
            self.assertFalse(result['verified'])
            self.assertIn('uncertain',result['message'])
        self.assertEqual(self.hass.services.async_call.await_count,1)

    async def test_native_volume_slot_is_validated_against_spoken_percentage(self):
        recognized={'intent':{'name':'HassSetVolume'},'targets':{'media_player.tv_fixture':{'matched':True}},
                    'details':{'volume_level':{'value':25}}}
        result=await self.pilot.recognized_control(self.request('Set Master Bedroom TV volume to 25 percent'),recognized)
        self.assertTrue(result['verified']);self.assertEqual(self.calls[0][2]['volume_level'],.25)

    async def test_room_mapping_drift_is_rejected_before_dispatch(self):
        self.bindings['gadget_lab_light']['area_id']='reviewed_room'
        with patch('custom_components.edsys_voice_knowledge.pilot.er.async_get_effective_area_id',return_value='changed_room'):
            with self.assertRaises(PilotRefusal):await self.pilot.control(self.request('Turn on Gadget Lab lights'),'gadget_lab_light','on')
        self.assertEqual(self.calls,[])

    async def test_open_plex_wakes_only_the_bound_tv_and_waits_for_source(self):
        self.states['media_player.tv_fixture']=State('media_player.tv_fixture','off',{'supported_features':int(F.TURN_ON|F.SELECT_SOURCE)})
        result=await self.pilot.control(self.request('Open Plex in the Master Bedroom'),'master_bedroom_tv','open_plex')
        self.assertTrue(result['verified'])
        self.assertEqual([row[1] for row in self.calls],['turn_on','select_source'])
        self.assertTrue(all(row[2]['entity_id']=='media_player.tv_fixture' for row in self.calls))

    async def test_router_uses_native_agent_constant_and_runs_without_backend(self):
        self.hass.data={'edsys_voice_knowledge':{'pilot':self.pilot}}
        self.hass.config=SimpleNamespace(language='en')
        native=SimpleNamespace(async_debug_recognize=AsyncMock(return_value={'match':True,'intent':{'name':'HassTurnOn'},'targets':{'light.fixture':{'matched':True}}}))
        entity=EdSysVoice(SimpleNamespace(entry_id='fixture'),'conversation.missing_backend');entity.hass=self.hass
        self.assertEqual(entity.supported_features,conversation.ConversationEntityFeature.CONTROL)
        user_input=conversation.ConversationInput(text='Turn on Gadget Lab lights',context=self.context,conversation_id=None,
            device_id='nimo_fixture',satellite_id=None,language='*',agent_id='conversation.fixture')
        with patch('homeassistant.components.conversation.async_get_agent',side_effect=lambda _,key:native if key==conversation.HOME_ASSISTANT_AGENT else None) as getter:
            result=await entity.async_process(user_input)
        self.assertIn('on',result.response.speech['plain']['speech'])
        getter.assert_called_once_with(self.hass,conversation.HOME_ASSISTANT_AGENT)
        self.assertEqual(native.async_debug_recognize.call_args.args[0].language,'en')
        self.assertEqual(len(self.calls),1)

    async def test_model_cannot_turn_an_uncertain_tool_result_into_spoken_success(self):
        self.hass.data={'edsys_voice_knowledge':{'pilot':self.pilot}}
        self.hass.config=SimpleNamespace(language='en')
        self.states['conversation.backend']=State('conversation.backend','ready')
        self.hass.services.async_call=AsyncMock(side_effect=HomeAssistantError('fixture transport failure'))
        native=SimpleNamespace(async_debug_recognize=AsyncMock(return_value={'match':False}))
        entity=EdSysVoice(SimpleNamespace(entry_id='fixture'),'conversation.backend');entity.hass=self.hass
        user_input=conversation.ConversationInput(text='Turn on Gadget Lab lights',context=self.context,conversation_id=None,
            device_id='nimo_fixture',satellite_id=None,language='en',agent_id='conversation.fixture')
        async def backend(**kwargs):
            from custom_components.edsys_voice_knowledge.pilot import CURRENT_REQUEST
            await self.pilot.control(CURRENT_REQUEST.get(),'gadget_lab_light','on')
            response=intent.IntentResponse(language='en');response.async_set_speech('Done, the light is on.')
            return conversation.ConversationResult(response=response,conversation_id=None)
        with patch('homeassistant.components.conversation.async_get_agent',return_value=native),patch('homeassistant.components.conversation.async_converse',side_effect=backend):
            result=await entity.async_process(user_input)
        self.assertIn('uncertain',result.response.speech['plain']['speech'])
        self.assertNotIn('light is on',result.response.speech['plain']['speech'])
        self.assertEqual(self.hass.services.async_call.await_count,1)


if __name__=='__main__':unittest.main()
