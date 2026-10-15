"""Manual Pi nighttime controls and an explicit, cancellable HA sleep timer."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import math
import time
from uuid import uuid4

import probatio
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from . import DOMAIN
from .bedroom_screen import ERRORS
from .pilot_policy import PilotRefusal

ACTIONS=('night_on','night_off','audio_options','subtitles_on','subtitles_off',
         'subtitles_english','subtitles_next','subtitles_normal','subtitles_large',
         'subtitles_extra_large','subtitles_earlier','subtitles_later',
         'subtitle_options','sleep_start','sleep_cancel')
MINUTES=(1,5,15,30,45,60,90,120)
COMPRESSION='audiooutput.maintainoriginalvolume'
FONT='subtitles.fontsize'
LABELS=['Player.SubtitleDelay','VideoPlayer.AudioChannels']


class BedroomComfort:
    def __init__(self,hass,screen):
        self.hass,self.screen=hass,screen
        self.listeners=[];self.lock=asyncio.Lock();self.available=False
        self.status={};self.timer={'state':'Off','remaining_seconds':0}
        self.cancel_timer=None;self.timer_key=None;self.deadline=0

    def changed(self):
        for listener in tuple(self.listeners):listener()

    async def rpc(self,method,**params):
        if method not in {'Settings.GetSettingValue','Settings.SetSettingValue',
                          'Player.GetProperties','Player.SetSubtitle','Player.PlayPause',
                          'XBMC.GetInfoLabels','Input.ExecuteAction','GUI.ActivateWindow'}:
            raise ValueError('Unsupported nighttime action')
        async with asyncio.timeout(5):return await self.screen.client().call_method(method,**params)

    async def video(self):
        # Player 1 is the native Kodi video player, not a discovered Plex client.
        return await self.rpc('Player.GetProperties',playerid=1,
                              properties=['speed','subtitleenabled','currentsubtitle','subtitles','currentaudiostream'])

    async def setting(self,name):
        return (await self.rpc('Settings.GetSettingValue',setting=name))['value']

    async def poll(self,_=None):
        try:
            compression=await self.setting(COMPRESSION);font=await self.setting(FONT)
            try:
                player=await self.video();video=True
            except ERRORS:
                player={};video=False
            labels=await self.rpc('XBMC.GetInfoLabels',labels=LABELS)
            subtitle=player.get('currentsubtitle',{})
            self.status={'video_active':video,'night_compression':compression,
                         'subtitle_size':font,'subtitles_enabled':player.get('subtitleenabled',False),
                         'subtitle_track':(' · '.join(str(subtitle.get(k,'')) for k in ['language','name'])).strip(' ·'),
                         'subtitle_delay':labels.get('Player.SubtitleDelay',''),
                         'audio_channels':player.get('currentaudiostream',{}).get('channels')}
            self.available=True
        except ERRORS:self.available=False
        self.update_countdown();self.changed()

    def update_countdown(self):
        if self.timer_key:
            seconds=max(0,math.ceil(self.deadline-time.monotonic()))
            self.timer.update(state=f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}',remaining_seconds=seconds)

    def cancel(self,state='Off'):
        self.timer_key=None;self.deadline=0
        if self.cancel_timer:self.cancel_timer();self.cancel_timer=None
        self.timer={'state':state,'remaining_seconds':0};self.changed()

    async def cancel_permission(self,context):
        user=await self.hass.auth.async_get_user(context.user_id) if context.user_id else None
        eid=self.screen.remote.config['targets']['pi']['entity_id']
        if user is None or not user.is_active or not user.permissions.check_entity(eid,POLICY_CONTROL):
            raise PilotRefusal('Your Home Assistant account cannot cancel this timer.')

    async def expire(self,key,context,_=None):
        async with self.lock:
            if key!=self.timer_key:return
            # Disarm before the one possible write; no resume or replay.
            self.cancel('Checking playback')
            try:
                await self.screen.authorize(context)
                player=await self.video()
                if player['speed']!=0:
                    await self.rpc('Player.PlayPause',playerid=1,play=False)
                verified=(await self.video())['speed']==0
                self.timer={'state':'Finished · Pi paused' if verified else 'Outcome uncertain',
                            'remaining_seconds':0}
            except ERRORS:
                self.timer={'state':'Not applied · check Pi/TV','remaining_seconds':0}
            self.changed()

    def arm(self,minutes,context):
        self.cancel();key=str(uuid4());self.timer_key=key
        self.deadline=time.monotonic()+minutes*60
        self.timer={'state':'Starting','remaining_seconds':minutes*60,
                    'finishes_at':(datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat(),
                    'finish_action':'Pause Pi playback; TV stays on','minutes':minutes}
        async def callback(now):await self.expire(key,context,now)
        self.cancel_timer=async_call_later(self.hass,minutes*60,callback)
        self.update_countdown();self.changed()

    async def handle(self,call):
        action=call.data['action'];minutes=call.data.get('minutes')
        if action not in ACTIONS or (minutes is not None and (action!='sleep_start' or type(minutes) is not int or minutes not in MINUTES)):
            raise ServiceValidationError('Choose an available nighttime control.')
        async with self.lock:
            try:
                if action=='sleep_cancel':
                    await self.cancel_permission(call.context);self.cancel();return {'cancelled':True}
                await self.screen.authorize(call.context)
                if action=='sleep_start':
                    if minutes not in MINUTES:raise PilotRefusal('Choose a sleep timer duration.')
                    await self.video();self.arm(minutes,call.context)
                    return {'armed':True,'minutes':minutes,'finish_action':self.timer['finish_action']}
                player=await self.video()
                if action.startswith('night_'):
                    if await self.setting('audiooutput.passthrough') or await self.setting('audiooutput.channels')!=1 or player.get('currentaudiostream',{}).get('channels',0)<=2:
                        raise PilotRefusal('Night compression requires multichannel audio downmixed to stereo, with passthrough off.')
                    value=action=='night_on'
                    await self.rpc('Settings.SetSettingValue',setting=COMPRESSION,value=value)
                    verified=await self.setting(COMPRESSION)==value
                elif action in {'audio_options','subtitle_options'}:
                    await self.screen.cancel();await self.screen.clear()
                    await self.rpc('GUI.ActivateWindow',window='osdaudiosettings' if action=='audio_options' else 'osdsubtitlesettings')
                    return {'submitted':True,'message':'Use the Pi navigation buttons; Back closes the native options.'}
                elif action in {'subtitles_normal','subtitles_large','subtitles_extra_large'}:
                    value={'subtitles_normal':42,'subtitles_large':54,'subtitles_extra_large':64}[action]
                    await self.rpc('Settings.SetSettingValue',setting=FONT,value=value)
                    verified=await self.setting(FONT)==value
                elif action in {'subtitles_earlier','subtitles_later'}:
                    before=(await self.rpc('XBMC.GetInfoLabels',labels=['Player.SubtitleDelay']))['Player.SubtitleDelay']
                    await self.rpc('Input.ExecuteAction',action='subtitledelayminus' if action=='subtitles_earlier' else 'subtitledelayplus')
                    after=(await self.rpc('XBMC.GetInfoLabels',labels=['Player.SubtitleDelay']))['Player.SubtitleDelay']
                    verified=bool(after) and before!=after
                else:
                    tracks=player.get('subtitles',[])
                    if not tracks:raise PilotRefusal('This video has no selectable subtitle tracks.')
                    desired={'subtitles_on':'on','subtitles_off':'off','subtitles_next':'next'}.get(action)
                    if action=='subtitles_english':
                        matches=[t for t in tracks if t.get('language') in {'eng','en'} and not t.get('isforced')]
                        if not matches:raise PilotRefusal('No full English subtitle track is available for this item.')
                        matches.sort(key=lambda t:(bool(t.get('isimpaired')),bool('sdh' in t.get('name','').lower()),t['index']))
                        desired=matches[0]['index']
                    await self.rpc('Player.SetSubtitle',playerid=1,subtitle=desired,enable=True)
                    actual=await self.video()
                    verified=actual.get('subtitleenabled') is (action!='subtitles_off')
                    if isinstance(desired,int):verified=verified and actual.get('currentsubtitle',{}).get('index')==desired
                await self.poll()
                return {'verified':verified,'action':action,'audible_or_visible_effect':'owner_confirmation_required'}
            except ERRORS as error:
                raise ServiceValidationError(str(error) if isinstance(error,PilotRefusal) else 'The nighttime action could not be confirmed. Check before retrying.') from None


async def setup_comfort(hass,entry):
    screen=hass.data[DOMAIN].get('bedroom_screen')
    if screen is None:return
    controller=BedroomComfort(hass,screen);hass.data[DOMAIN]['bedroom_comfort']=controller
    schema=probatio.Schema({probatio.Required('action'):probatio.In(ACTIONS),
                           probatio.Optional('minutes'):probatio.In(MINUTES)})
    hass.services.async_register(DOMAIN,'bedroom_comfort',controller.handle,schema=schema,supports_response=SupportsResponse.OPTIONAL)
    entry.async_on_unload(lambda:hass.services.async_remove(DOMAIN,'bedroom_comfort'))
    entry.async_on_unload(async_track_time_interval(hass,controller.poll,timedelta(seconds=5)))
    entry.async_on_unload(controller.cancel)
    entry.async_on_unload(lambda:hass.data[DOMAIN].pop('bedroom_comfort',None))
    await controller.poll()
