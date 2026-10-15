"""Native-first bedroom media controls and cloud tools over fixed remote actions."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
import time

import probatio
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from . import DOMAIN
from .pilot import CURRENT_REQUEST
from .pilot_policy import PilotRefusal, normalize
from .remote_policy import OPERATIONS, TV_ONLY, PLAYBACK, local_command, media_text, recipe, validate

UNKNOWN = {'status':'uncertain','verified':False,
           'message':'That remote command’s outcome is uncertain. Check the screen before trying again.'}
FEATURES = {'play':F.PLAY,'pause':F.PAUSE,'stop':F.STOP,'previous':F.PREVIOUS_TRACK,'next':F.NEXT_TRACK,
            'seek':F.SEEK,'power_on':F.TURN_ON,'power_off':F.TURN_OFF,'set_volume':F.VOLUME_SET,
            'mute':F.VOLUME_MUTE,'unmute':F.VOLUME_MUTE,'volume_up':F.VOLUME_STEP,'volume_down':F.VOLUME_STEP,
            'select_source':F.SELECT_SOURCE}


class MediaRemote:
    def __init__(self,hass,pilot,config):
        self.hass,self.pilot,self.config=hass,pilot,config
        self.pilot.media_bindings={'remote_'+k:v for k,v in config['targets'].items()}
        self.lock=asyncio.Lock();self.seen={}

    async def state(self,target,context,*,control=False):
        return await self.pilot.state('remote_'+target,context,control=control)

    def selected(self,tv):
        app=str(tv.attributes.get('app_id','')).lower()
        source=normalize(str(tv.attributes.get('source','')))
        if app:return 'pi' if app=='com.webos.app.hdmi1' else 'tv'
        if source in {normalize(s) for s in self.config['pi_sources']}:return 'pi'
        return 'tv'

    async def lookup(self,context):
        targets={}
        for target in self.config['targets']:
            try:
                s=await self.state(target,context)
                a=s.attributes
                targets[target]={'name':self.config['targets'][target]['name'],'state':s.state,
                                 'source':a.get('source'),'sources':list(a.get('source_list',[]))[:40],
                                 'title':str(a.get('media_title',''))[:120],
                                 'series':str(a.get('media_series_title',''))[:120],
                                 'position_seconds':a.get('media_position'),'duration_seconds':a.get('media_duration'),
                                 'volume_percent':round(a['volume_level']*100) if isinstance(a.get('volume_level'),(int,float)) else None,
                                 'muted':a.get('is_volume_muted')}
            except PilotRefusal as error:targets[target]={'available':False,'message':str(error)}
        try:active=self.selected(await self.state('tv',context))
        except PilotRefusal:active=None
        return {'scope':'Master Bedroom LG and Pi Plex remotes','active_player':active,
                'targets':targets,'operations':OPERATIONS,
                'rules':'One immediate command. Power/input/volume use LG. Active player follows visible input. Explicit LG/Pi overrides navigation/playback. Bare bedroom is ambiguous. Button acknowledgement does not verify the screen. Titles/URLs cannot be supplied to control.'}

    async def local(self,request,recognized=None):
        targets=[k for k,v in (recognized or {}).get('targets',{}).items() if v.get('matched')]
        known={b['entity_id'] for b in self.config['targets'].values()}
        reviewed=[t for t in targets if t in known]
        if targets and reviewed and len(targets)!=1:
            if any(w in normalize(request.text).split() for w in ('all','both','and')):raise PilotRefusal('Please name one screen at a time.')
            return {'delegate_media':True}
        if targets and not reviewed:
            if not any(w in normalize(request.text).split() for w in ('pi','kodi','lg','plex')):return None
            if not media_text(request.text):return None
            return {'delegate_media':True}
        if not media_text(request.text) and not reviewed:return None
        command=local_command(request.text)
        if command:
            try:return await self.control(request,*command)
            except (TimeoutError,HomeAssistantError):
                request.outcome=dict(UNKNOWN)
                return request.outcome
        # A known media target must not fall through to unvalidated native
        # dispatch. Let the existing cloud backend interpret the remaining text.
        return {'delegate_media':True}

    async def _call(self,request,domain,service,data):
        request.control_attempted=True;request.outcome=dict(UNKNOWN)
        async with asyncio.timeout(20):
            await self.hass.services.async_call(domain,service,data,blocking=True,context=request.context)

    async def _refresh(self,request,entity_id):
        try:
            async with asyncio.timeout(5):
                await self.hass.services.async_call('homeassistant','update_entity',{'entity_id':entity_id},blocking=True,context=request.context)
        except (TimeoutError,HomeAssistantError):pass

    async def control(self,request,target,operation,value=None,source=None):
        validate(request.text,target,operation,value,source)
        try:
            return await self._control(request,target,operation,value,source)
        except (TimeoutError,HomeAssistantError,PilotRefusal):
            if not request.control_attempted:raise
            request.outcome=dict(UNKNOWN)
            self.seen[request.context.id]=(time.monotonic(),request.outcome)
            return request.outcome

    async def _control(self,request,target,operation,value,source):
        async with self.lock:
            now=time.monotonic();self.seen={k:v for k,v in self.seen.items() if now-v[0]<120}
            key=request.context.id
            if key in self.seen:return self.seen[key][1]
            if request.control_attempted:return request.outcome or dict(UNKNOWN)
            if len(self.seen)>=128:raise PilotRefusal('The remote is busy. Try a fresh command shortly.')
            q=normalize(request.text)
            explicit_pi=bool(re.search(r'\b(pi|raspberry|kodi)\b',q))
            explicit_tv=bool(re.search(r'\b(lg|tv remote|television remote)\b',q)) and not explicit_pi
            if operation not in TV_ONLY:
                if explicit_pi and target=='tv' or explicit_tv and target=='pi':raise PilotRefusal('The requested screen does not match that remote target.')
                if explicit_pi:target='pi'
                elif explicit_tv:target='tv'
            if operation in TV_ONLY:target='tv'
            if target=='active':
                tv=await self.state('tv',request.context,control=True)
                if tv.state=='off':raise PilotRefusal('The Master Bedroom TV is off. Wake it first, or explicitly name the Pi for background playback.')
                target=self.selected(tv)
            s=await self.state(target,request.context,control=True)
            if target=='tv' and s.state=='off' and operation not in {'power_on','power_off','open_plex'}:
                raise PilotRefusal('The Master Bedroom TV is off. Ask to wake it first.')
            if operation in PLAYBACK|{'rewind','fast_forward','subtitles_on','subtitles_off','captions','now_playing'} and target=='pi' and operation!='now_playing' and s.state not in {'playing','paused','buffering'}:
                raise PilotRefusal('There is no active Pi playback. Open Plex and choose something first.')
            if operation=='seek_relative':
                await self._refresh(request,s.entity_id)
                s=await self.state(target,request.context,control=True)
                pos=s.attributes.get('media_position');duration=s.attributes.get('media_duration')
                if not isinstance(pos,(int,float)) or not isinstance(duration,(int,float)):raise PilotRefusal('Current playback position is unavailable.')
                value=max(0,min(duration-.1,pos+value));operation='seek'
            if operation=='seek':
                duration=s.attributes.get('media_duration')
                if not isinstance(duration,(int,float)) or not 0<=value<duration:raise PilotRefusal('That position is outside the current item.')
            if operation=='select_source':
                sources=[v for v in s.attributes.get('source_list',[]) if normalize(v)==normalize(source)]
                if len(sources)!=1:raise PilotRefusal('That app or input is not uniquely available on the TV.')
                source=sources[0]
            if operation=='open_plex' and target=='tv':
                if s.state=='off':
                    if not s.attributes.get('supported_features',0)&F.TURN_ON:raise PilotRefusal('TV wake-up is not advertised by this device.')
                    self.seen[key]=(now,dict(UNKNOWN))
                    try:await self._call(request,'media_player','turn_on',{'entity_id':s.entity_id})
                    except (TimeoutError,HomeAssistantError):return request.outcome
                    for _ in range(25):
                        await asyncio.sleep(.4);s=await self.state('tv',request.context,control=True)
                        if s.state!='off' and s.attributes.get('source_list'):break
                    if s.state=='off':raise PilotRefusal('The TV has not reported awake yet.')
                sources=[v for v in s.attributes.get('source_list',[]) if normalize(v)=='plex']
                if len(sources)!=1:raise PilotRefusal('Plex is not uniquely available on the LG.')
                operation,source='select_source',sources[0]
            if operation=='channel':
                # A channel number is exactly the bounded keypad sequence plus
                # ENTER; it never launches another app or guesses a tuner input.
                if normalize(str(s.attributes.get('source','')))!='live tv':raise PilotRefusal('Open Live TV before selecting a channel number.')
                sequence=[('webostv','button',{'button':digit}) for digit in str(int(value))]+[('webostv','button',{'button':'ENTER'})]
            else:
                if operation=='skip_back' and target=='tv':operation='rewind'
                if operation=='skip_forward' and target=='tv':operation='fast_forward'
                domain,service,data=recipe(target,operation,value,source)
                sequence=[(domain,service,data)]
            feature=FEATURES.get(operation)
            if feature and not s.attributes.get('supported_features',0)&feature:
                raise PilotRefusal('That action is not currently advertised by this player.')
            label=self.config['targets'][target]['name']
            expected=None
            if operation in {'play','pause','stop','power_on','power_off'}:
                states={'play':{'playing'},'pause':{'paused'},'stop':{'idle','off'},'power_on':{'on','idle','playing','paused'},'power_off':{'off'}}[operation]
                # LG reports on/off rather than the internal app transport.
                if target=='pi' or operation.startswith('power_'):expected=lambda x:x.state in states
            elif operation in {'mute','unmute'}:expected=lambda x:x.attributes.get('is_volume_muted') is (operation=='mute')
            elif operation=='set_volume':expected=lambda x:isinstance(x.attributes.get('volume_level'),(int,float)) and abs(x.attributes['volume_level']-value/100)<.005
            elif operation in {'volume_up','volume_down'}:
                before=s.attributes.get('volume_level')
                if isinstance(before,(int,float)):
                    expected=lambda x:isinstance(x.attributes.get('volume_level'),(int,float)) and ((x.attributes['volume_level']>before if operation=='volume_up' else x.attributes['volume_level']<before) or before==(1 if operation=='volume_up' else 0))
            elif operation=='select_source':expected=lambda x:x.attributes.get('source')==source
            elif operation=='hdmi1':expected=lambda x:self.selected(x)=='pi'
            elif operation=='hdmi2':expected=lambda x:str(x.attributes.get('app_id','')).lower()=='com.webos.app.hdmi2'
            elif operation=='seek':expected=lambda x:isinstance(x.attributes.get('media_position'),(int,float)) and abs(x.attributes['media_position']-value)<4
            result=dict(UNKNOWN);self.seen[key]=(now,result)
            try:
                request.control_attempted=True;request.outcome=result
                # These services set an explicit desired state. Send it once
                # even if a polled snapshot already matches: the actual player
                # may have changed since the last Kodi poll.
                explicit_state=operation in {'play','pause','stop','mute','unmute','set_volume','seek'}
                if expected is None or explicit_state or not expected(s):
                    for domain,service,data in sequence:
                        # Each stage rechecks mapping/permission/exposure. Only
                        # fixed command recipes can reach this call.
                        s=await self.state(target,request.context,control=True)
                        await self._call(request,domain,service,{'entity_id':s.entity_id,**data})
                    if expected:
                        await self._refresh(request,s.entity_id)
                        async with asyncio.timeout(14):
                            while True:
                                current=await self.state(target,request.context,control=True)
                                if expected(current):break
                                await asyncio.sleep(.3)
                status='verified' if expected else 'submitted'
                detail=operation.replace('_',' ')
                if operation=='set_volume':detail=f'volume set to {round(value)} percent'
                elif operation=='seek':detail=f'playback at {int(value)//60} minutes {int(value)%60} seconds'
                elif operation=='select_source':detail=f'{source} selected'
                message=f'{label}: {detail}.' if expected else f'Sent {detail} to {label}.'
                result={'status':status,'verified':bool(expected),'target':target,'operation':operation,'message':message}
            except (TimeoutError,HomeAssistantError,PilotRefusal):
                # Some recipe may already have reached the device. Preserve
                # uncertainty and never start it again after a timeout/failure.
                result=dict(UNKNOWN)
            request.outcome=result;self.seen[key]=(now,result)
            return result


def runtime(hass):
    value=hass.data.get(DOMAIN,{}).get('media_remote')
    if value is None:raise PilotRefusal('Bedroom remote voice controls are unavailable.')
    return value


class LookupMediaRemote(llm.Tool):
    name='lookup_bedroom_media'
    description='Read the Master Bedroom LG/Pi player, current input, current media, available TV apps/inputs and remote action vocabulary. Use before a media command. No URLs, credentials, images or unrelated household data.'
    integration=DOMAIN
    parameters=probatio.Schema({})
    annotations=llm.ToolAnnotations(read_only=True,destructive=False,idempotent=True,open_world=False)

    async def async_call(self,hass,tool_input,llm_context):
        try:return llm.ToolResult(data=await runtime(hass).lookup(llm_context.context))
        except PilotRefusal as error:return llm.ToolResult(data={'message':str(error)},error=True)


class ControlMediaRemote(llm.Tool):
    name='control_bedroom_media'
    description='Execute one current spoken remote command. active follows the LG input; tv explicitly addresses LG, pi addresses Kodi. Volume/power/input use LG. value is percent 0-100 for set_volume, seconds for seek, signed seconds for seek_relative, or a digit/channel number; otherwise null. source is a current TV app/input label only for select_source, otherwise null. No arbitrary service, URL, title, script, key repetition or scheduling. submitted means key sent, not verified screen outcome.'
    integration=DOMAIN
    parameters=probatio.Schema({probatio.Required('target'):probatio.In(['active','tv','pi']),
        probatio.Required('operation'):probatio.In(OPERATIONS),
        probatio.Required('value'):probatio.Any(None,probatio.All(probatio.Coerce(float),probatio.Range(min=-86400,max=99999))),
        probatio.Required('source'):probatio.Any(None,probatio.All(str,probatio.Length(min=1,max=80)))})
    annotations=llm.ToolAnnotations(read_only=False,destructive=False,idempotent=False,open_world=False)

    async def async_call(self,hass,tool_input,llm_context):
        request=CURRENT_REQUEST.get()
        if request is None or request.context.id!=getattr(llm_context.context,'id',None):return llm.ToolResult(data={'message':'No authenticated current voice request.'},error=True)
        try:
            data=await runtime(hass).control(request,**tool_input.tool_args)
            return llm.ToolResult(data=data,error=data.get('status') not in {'verified','submitted'})
        except PilotRefusal as error:
            if not request.control_attempted:request.outcome={'status':'refused','verified':False,'message':str(error)}
            return llm.ToolResult(data=request.outcome or {'message':str(error)},error=True)
        except (TimeoutError,HomeAssistantError):
            request.outcome=dict(UNKNOWN)
            return llm.ToolResult(data=request.outcome,error=True)


async def setup_media_remote(hass,entry):
    path=Path(hass.config.path(DOMAIN,'media_remote.json'))
    if not path.exists():return
    if path.is_symlink() or path.stat().st_size>16384:raise ValueError('Invalid media remote configuration')
    config=await hass.async_add_executor_job(lambda:json.loads(path.read_text()))
    if config.get('version')!=1 or set(config.get('targets',{}))!={'tv','pi'} or not 1<=len(config.get('pi_sources',[]))<=8:raise ValueError('Unexpected media remote scope')
    if not all(isinstance(s,str) and 1<=len(s)<=80 for s in config['pi_sources']):raise ValueError('Invalid Pi input labels')
    for binding in config['targets'].values():
        if not isinstance(binding,dict) or not {'entity_id','device_id','config_entry_id','name','area_id'}<=binding.keys():raise ValueError('Incomplete reviewed media binding')
        if not all(isinstance(binding[k],str) and binding[k] for k in ('entity_id','device_id','config_entry_id','name')) or not binding['entity_id'].startswith('media_player.'):raise ValueError('Invalid media identity')
    pilot=hass.data.get(DOMAIN,{}).get('pilot')
    if pilot is None:raise ValueError('Reviewed pilot is required')
    hass.data[DOMAIN]['media_remote']=MediaRemote(hass,pilot,config)
    entry.async_on_unload(lambda:hass.data.get(DOMAIN,{}).pop('media_remote',None))
