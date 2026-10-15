"""Manual screen transport/permissions without camera, Kodi or device I/O."""
import asyncio
import base64
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from PIL import Image
from homeassistant.core import Context,State
from homeassistant.exceptions import ServiceValidationError
from custom_components.edsys_voice_knowledge.bedroom_screen import BedroomScreen,jpeg_frame
from custom_components.edsys_voice_knowledge.screen_protocol import INFO_LABEL,MAX_JPEG,unseal
import native_media_checks as media_checks
import native_pilot_checks as pilot_checks


class NativeScreenChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUpFixture=pilot_checks.NativePilotChecks.asyncSetUp
    asyncTearDownFixture=media_checks.NativeMediaChecks.asyncTearDown

    async def asyncSetUp(self):
        await media_checks.NativeMediaChecks.asyncSetUp(self)
        self.config={'transport_key':'11'*32,'sources':{k:{'entity_id':eid,'device_id':'fixture','config_entry_id':'fixture','name':k} for k,eid in [('family','calendar.family_fixture'),('home','calendar.home_fixture'),('reminders','todo.fixture'),('bedside','light.bedside_fixture')]}}
        for b in self.config['sources'].values():self.states[b['entity_id']]=State(b['entity_id'],'on')
        self.sent=[];self.render={'version':1,'view':'idle','visible':False,'frames':0};self.fail_transport=False
        async def rpc(method,**data):
            if method=='XBMC.GetInfoLabels':return {INFO_LABEL:__import__('json').dumps(self.render)}
            self.sent.append((method,data))
            if self.fail_transport:raise TimeoutError
            m=unseal(data['data'],bytes.fromhex(self.config['transport_key']));self.render={'version':1,'request_id':m['request_id'],'view':m.get('kind','camera'),'visible':m['command']!='clear','frames':1 if m['command']=='camera_frame' else 0}
            if m['command']=='clear':self.render['view']='idle'
            return 'OK'
        self.hass.config_entries=SimpleNamespace(async_get_entry=lambda _:SimpleNamespace(domain='kodi',runtime_data=SimpleNamespace(kodi=SimpleNamespace(call_method=rpc))))
        self.hass.config.time_zone='America/New_York'
        async def executor(fn,*args):return fn(*args)
        self.hass.async_add_executor_job=executor
        self.hass.async_create_task=lambda coro,name:asyncio.create_task(coro,name=name)
        self.screen=BedroomScreen(self.hass,self.pilot,self.remote,self.config)

    async def asyncTearDown(self):
        await self.screen.cancel();await self.asyncTearDownFixture()

    def call(self,action,**data):return SimpleNamespace(context=Context(user_id='fixture'),data={'action':action,**data})

    async def test_manual_card_is_acknowledged_without_player_service(self):
        result=await self.screen.handle(self.call('media'))
        self.assertTrue(result['rendered']);self.assertEqual(result['view'],'media')
        self.assertEqual(len(self.sent),1);self.assertEqual(self.calls,[])
        self.assertNotIn('file',self.sent[0][1]['data'])

    async def test_camera_bytes_are_bounded_local_and_never_a_url(self):
        raw=io.BytesIO();Image.new('RGB',(640,360),'blue').save(raw,format='PNG')
        with patch('custom_components.edsys_voice_knowledge.bedroom_screen.async_get_image',AsyncMock(return_value=SimpleNamespace(content=raw.getvalue()))):
            result=await self.screen.handle(self.call('camera_popup',camera='front_porch'))
        self.assertTrue(result['rendered']);self.assertEqual(result['frames'],1)
        frame=unseal(self.sent[0][1]['data'],self.screen.key);self.assertIn('jpeg',frame);self.assertNotIn('url',frame)
        self.assertLessEqual(len(base64.b64decode(frame['jpeg'])),MAX_JPEG)
        self.assertEqual(self.calls,[])

    async def test_permissions_exposure_identity_and_wrong_tv_input_block_before_send(self):
        for scenario in ['permission','exposure','identity','input']:
            self.authorized=self.exposed=True;self.registry.config_entry_id='fixture'
            if scenario=='permission':self.authorized=False
            if scenario=='exposure':self.exposed=False
            if scenario=='identity':self.registry.config_entry_id='changed'
            if scenario=='input':
                s=self.states['media_player.tv_fixture'];self.states[s.entity_id]=State(s.entity_id,'on',{**s.attributes,'app_id':'netflix'})
            with self.subTest(scenario=scenario),self.assertRaises(ServiceValidationError):await self.screen.handle(self.call('butler'))
        self.assertEqual(self.sent,[]);self.assertEqual(self.calls,[])

    async def test_unknown_camera_and_unauthenticated_call_do_not_fetch(self):
        with self.assertRaises(ServiceValidationError):await self.screen.handle(self.call('camera_popup',camera='unreviewed'))
        call=self.call('clock');call.context=Context()
        with self.assertRaises(ServiceValidationError):await self.screen.handle(call)
        self.assertEqual(self.sent,[])

    async def test_transport_timeout_is_not_retried(self):
        self.fail_transport=True
        with self.assertRaises(ServiceValidationError):await self.screen.handle(self.call('butler'))
        self.assertEqual(len(self.sent),1)

    async def test_clear_does_not_require_visible_pi_input_or_play_media(self):
        s=self.states['media_player.tv_fixture'];self.states[s.entity_id]=State(s.entity_id,'off',s.attributes)
        result=await self.screen.handle(self.call('clear'))
        self.assertFalse(result['visible']);self.assertEqual(self.calls,[])

    async def test_original_client_compatibility_failure_has_no_credential_fallback(self):
        self.hass.config_entries.async_get_entry=lambda _:SimpleNamespace(domain='kodi')
        with self.assertRaises(ServiceValidationError):await self.screen.handle(self.call('butler'))
        self.assertEqual(self.sent,[])

    def test_jpeg_size_and_metadata_boundary(self):
        raw=io.BytesIO();Image.new('RGB',(3000,2000),'green').save(raw,format='PNG')
        output=base64.b64decode(jpeg_frame(raw.getvalue()))
        self.assertLessEqual(len(output),MAX_JPEG)
        with Image.open(io.BytesIO(output)) as image:
            self.assertLessEqual(image.width,1280);self.assertLessEqual(image.height,720);self.assertFalse(image.getexif())


if __name__=='__main__':unittest.main()
