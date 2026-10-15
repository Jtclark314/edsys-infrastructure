"""Nighttime controls and timer expiry use fake Kodi, never real media/TV."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from homeassistant.exceptions import ServiceValidationError
from custom_components.edsys_voice_knowledge.bedroom_comfort import BedroomComfort,COMPRESSION,FONT
import native_media_checks as media_checks
import native_pilot_checks as pilot_checks
import native_screen_checks as screen_checks


class NativeComfortChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUpFixture=pilot_checks.NativePilotChecks.asyncSetUp
    asyncTearDownFixture=media_checks.NativeMediaChecks.asyncTearDown

    async def asyncSetUp(self):
        await screen_checks.NativeScreenChecks.asyncSetUp(self)
        self.comfort=BedroomComfort(self.hass,self.screen)
        self.settings={COMPRESSION:True,FONT:42,'audiooutput.passthrough':False,'audiooutput.channels':1}
        self.player={'speed':1,'subtitleenabled':False,'currentaudiostream':{'channels':6},
                     'currentsubtitle':{'index':0,'language':'eng','name':'Forced'},
                     'subtitles':[{'index':0,'language':'eng','isforced':True}, {'index':1,'language':'eng','isforced':False,'name':'Full'}]}
        self.rpc_calls=[];self.fail_write=False
        async def rpc(method,**p):
            self.rpc_calls.append((method,p))
            if method=='Settings.GetSettingValue':return {'value':self.settings[p['setting']]}
            if method=='Player.GetProperties':return dict(self.player)
            if method=='XBMC.GetInfoLabels':return {k:'0.000 s' for k in p['labels']}
            if self.fail_write:raise TimeoutError
            if method=='Settings.SetSettingValue':self.settings[p['setting']]=p['value']
            if method=='Player.PlayPause':self.player['speed']=0
            if method=='Player.SetSubtitle':
                self.player['subtitleenabled']=p['subtitle']!='off'
                if isinstance(p['subtitle'],int):self.player['currentsubtitle']={'index':p['subtitle'],'language':'eng'}
            return 'OK'
        self.comfort.rpc=rpc

    async def asyncTearDown(self):
        self.comfort.cancel();await screen_checks.NativeScreenChecks.asyncTearDown(self)

    def call(self,action,**data):return screen_checks.NativeScreenChecks.call(self,action,**data)

    def writes(self):return [(m,p) for m,p in self.rpc_calls if m not in {'Player.GetProperties','Settings.GetSettingValue','XBMC.GetInfoLabels'}]

    async def test_english_chooses_full_track_instead_of_forced_and_font_is_bounded(self):
        result=await self.comfort.handle(self.call('subtitles_english'));self.assertTrue(result['verified'])
        self.assertEqual(self.player['currentsubtitle']['index'],1)
        await self.comfort.handle(self.call('subtitles_large'));self.assertEqual(self.settings[FONT],54)
        self.assertNotIn('Player.PlayPause',[m for m,_ in self.writes()])

    async def test_night_compression_refuses_passthrough_stereo_and_unavailable_english(self):
        self.settings['audiooutput.passthrough']=True
        with self.assertRaises(ServiceValidationError):await self.comfort.handle(self.call('night_on'))
        self.settings['audiooutput.passthrough']=False;self.player['currentaudiostream']['channels']=2
        with self.assertRaises(ServiceValidationError):await self.comfort.handle(self.call('night_on'))
        self.player['subtitles']=self.player['subtitles'][:1]
        with self.assertRaises(ServiceValidationError):await self.comfort.handle(self.call('subtitles_english'))
        self.assertEqual(self.writes(),[])

    async def test_permissions_and_invalid_action_or_timer_scope_block_before_writes(self):
        for action,data in [('sleep_start',{'minutes':True}),('shell',{}),('sleep_cancel',{'minutes':30})]:
            with self.assertRaises(ServiceValidationError):await self.comfort.handle(self.call(action,**data))
        self.authorized=False
        for action in ['night_off','sleep_cancel']:
            with self.assertRaises(ServiceValidationError):await self.comfort.handle(self.call(action))
        self.assertEqual(self.writes(),[])

    async def test_timer_cancel_old_callback_restart_and_expiry_never_resume_or_power_off(self):
        with patch('custom_components.edsys_voice_knowledge.bedroom_comfort.async_call_later',return_value=lambda:None):
            await self.comfort.handle(self.call('sleep_start',minutes=30));key=self.comfort.timer_key
            await self.comfort.handle(self.call('sleep_cancel'));await self.comfort.expire(key,self.call('x').context)
            self.assertEqual(self.writes(),[])
            await self.comfort.handle(self.call('sleep_start',minutes=15));key=self.comfort.timer_key
            await self.comfort.expire(key,self.call('x').context)
        self.assertEqual(self.writes(),[('Player.PlayPause',{'playerid':1,'play':False})])
        self.assertIsNone(self.comfort.timer_key);self.assertIn('Pi paused',self.comfort.timer['state'])
        await self.comfort.expire(key,self.call('x').context);self.assertEqual(len(self.writes()),1)
        fresh=BedroomComfort(self.hass,self.screen);self.assertIsNone(fresh.timer_key)

    async def test_expiry_on_other_input_does_not_pause_and_cancel_can_work_offline(self):
        with patch('custom_components.edsys_voice_knowledge.bedroom_comfort.async_call_later',return_value=lambda:None):
            await self.comfort.handle(self.call('sleep_start',minutes=15));key=self.comfort.timer_key
        self.screen.authorize=unittest.mock.AsyncMock(side_effect=ServiceValidationError('Other input'))
        await self.comfort.expire(key,self.call('x').context)
        self.assertEqual(self.writes(),[]);self.assertIn('Not applied',self.comfort.timer['state'])
        await self.comfort.handle(self.call('sleep_cancel'));self.assertEqual(self.comfort.timer['state'],'Off')

    async def test_timeout_has_one_write_and_no_retry(self):
        self.fail_write=True
        with self.assertRaises(ServiceValidationError):await self.comfort.handle(self.call('night_off'))
        self.assertEqual(len(self.writes()),1)


if __name__=='__main__':unittest.main()
