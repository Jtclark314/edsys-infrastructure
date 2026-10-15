"""Authenticated manual dashboard views through the existing native Kodi client.

Frames and household records stay local. No LLM tool or new network listener.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import time
from uuid import uuid4

import probatio
from PIL import Image, ImageOps
from homeassistant.components.camera import async_get_image
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from jsonrpc_base.jsonrpc import ProtocolError, TransportError

from . import DOMAIN
from .pilot_policy import PilotRefusal
from .screen_protocol import INFO_LABEL,MAX_JPEG,MESSAGE,SENDER,ART_NAMES,seal

ACTIONS=('camera_popup','camera_large','clear','butler','clock','dark','media','briefing','household','connections','art','art_next')
ERRORS=(HomeAssistantError,TimeoutError,ProtocolError,TransportError,OSError,ValueError)


def jpeg_frame(raw):
    """Remove source metadata and bound the encoded image before leaving HA."""
    with Image.open(io.BytesIO(raw)) as image:
        image=image.convert('RGB')
        for size in ((1280,720),(960,540),(640,360)):
            bounded=ImageOps.contain(image,size)
            for quality in (75,55,35):
                output=io.BytesIO();bounded.save(output,format='JPEG',quality=quality)
                if len(output.getvalue())<=MAX_JPEG:return base64.b64encode(output.getvalue()).decode('ascii')
    raise ValueError('Camera image could not be bounded')


class BedroomScreen:
    def __init__(self,hass,pilot,remote,config):
        self.hass,self.pilot,self.remote,self.config=hass,pilot,remote,config
        self.key=bytes.fromhex(config['transport_key'])
        if len(self.key)!=32:raise ValueError('Invalid screen transport key')
        self.pilot.media_bindings.update({'screen_'+k:v for k,v in config['sources'].items()})
        self.lock=asyncio.Lock();self.task=None;self.active=None;self.listeners=[]
        self.status={'view':'starting','visible':False};self.available=False

    def changed(self):
        for listener in tuple(self.listeners):listener()

    def client(self):
        binding=self.remote.config['targets']['pi']
        row=er.async_get(self.hass).async_get(binding['entity_id'])
        if not row or row.disabled_by or any(getattr(row,k)!=binding[k] for k in ('device_id','config_entry_id')):
            raise PilotRefusal('The Pi mapping changed; review its connection.')
        entry=self.hass.config_entries.async_get_entry(binding['config_entry_id'])
        if entry is None or entry.domain!='kodi':raise PilotRefusal('The native Pi connection is unavailable.')
        runtime=getattr(entry,'runtime_data',None);client=getattr(runtime,'kodi',None)
        if not client or not callable(getattr(client,'call_method',None)):raise PilotRefusal('The Pi connection needs qualification after a Home Assistant update.')
        return client

    async def rpc(self,method,**params):
        if method not in {'JSONRPC.NotifyAll','XBMC.GetInfoLabels'}:raise ValueError('Unsupported display transport method')
        async with asyncio.timeout(5):return await self.client().call_method(method,**params)

    async def read(self):
        result=await self.rpc('XBMC.GetInfoLabels',labels=[INFO_LABEL])
        raw=result.get(INFO_LABEL,'')
        if not isinstance(raw,str) or not 1<=len(raw)<=2048:raise PilotRefusal('The Pi screen add-on is not ready.')
        result=json.loads(raw)
        if result.get('version')!=1:raise PilotRefusal('The Pi screen version needs review.')
        self.status=result;self.available=result.get('view')!='offline';self.changed()
        return result

    async def poll(self,_=None):
        try:await self.read()
        except ERRORS:
            self.available=False;self.changed()

    async def authorize(self,context,*,visible=True):
        await self.remote.state('pi',context,control=True)
        if visible:
            tv=await self.remote.state('tv',context)
            if tv.state=='off' or self.remote.selected(tv)!='pi':raise PilotRefusal('Choose Show Pi / HDMI 1 before opening a TV screen card.')

    def envelope(self,key,command,expires,sequence=0,**data):
        return dict(version=1,request_id=key,sequence=sequence,issued_at=time.time(),expires_at=expires,command=command,**data)

    async def send(self,message):
        # The native Kodi client's credentials and transport remain in HA.
        # Calling it directly avoids emitting image bytes on Kodi result events.
        # PM4K observes and logs Kodi notifications. Encrypt content so those
        # logs contain neither camera pixels nor household records.
        await self.rpc('JSONRPC.NotifyAll',sender=SENDER,message=MESSAGE,data=seal(message,self.key))

    async def wait_render(self,key,*,visible=True):
        async with asyncio.timeout(5):
            while True:
                status=await self.read()
                if status.get('request_id')==key and bool(status.get('visible'))==visible:return status
                await asyncio.sleep(.2)

    async def cancel(self):
        self.active=None
        if self.task:
            task,self.task=self.task,None;task.cancel()
            try:await task
            except asyncio.CancelledError:pass

    async def clear(self):
        key=str(uuid4());await self.send(self.envelope(key,'clear',time.time()+10))
        return await self.wait_render(key,visible=False)

    async def camera_message(self,key,expires,sequence,camera,mode,context):
        await self.authorize(context)
        await self.pilot.state(camera,context)
        binding=self.pilot.config['cameras'][camera]
        async with asyncio.timeout(7):
            image=await async_get_image(self.hass,binding['entity_id'],timeout=5,width=1280,height=720)
            jpeg=await self.hass.async_add_executor_job(jpeg_frame,image.content)
        return self.envelope(key,'camera_frame',expires,sequence,label=binding['name'],mode=mode,jpeg=jpeg)

    async def camera_loop(self,key,expires,camera,mode,context):
        sequence=1
        try:
            while self.active==key and time.time()<expires-1:
                await asyncio.sleep(1)
                status=await self.read()
                if status.get('request_id')!=key or not status.get('visible'):return
                message=await self.camera_message(key,expires,sequence,camera,mode,context)
                if self.active!=key:return
                await self.send(message);sequence+=1
        except asyncio.CancelledError:raise
        except ERRORS:
            self.status={'view':'preview_unavailable','visible':False};self.changed()
        finally:
            if self.active==key:
                self.active=None
                try:await self.clear()
                except ERRORS:pass

    async def source(self,key,context):return await self.pilot.state('screen_'+key,context)

    async def card(self,action,context):
        now=dt_util.now();heading=action.capitalize();body='';rows=[]
        if action=='butler':heading='At your service.';body='Use the Bedroom Pi dashboard or Nimo voice for your next request.'
        elif action in {'clock','dark'}:heading='Bedroom screen'
        elif action=='media':
            s=await self.remote.state('pi',context);a=s.attributes
            heading=str(a.get('media_title') or 'Pi media')
            body=' · '.join(str(v) for v in [a.get('media_series_title'),s.state] if v)
            if isinstance(a.get('media_position'),(int,float)):
                rows.append('Position · '+str(int(a['media_position'])//60)+' minutes')
            rows.append('Playback controls remain on your dashboard.')
        elif action=='household':
            heading='Before you turn in.';body='Current bedroom devices'
            for key in ('bedside',):
                s=await self.source(key,context);rows.append(self.config['sources'][key]['name']+' · '+s.state)
            for key in ('tv','pi'):
                s=await self.remote.state(key,context);rows.append(self.remote.config['targets'][key]['name']+' · '+s.state)
            rows.append('Door and temperature sensors are not configured.')
        elif action=='connections':
            heading='Device connections';body='Current Home Assistant availability'
            for key,binding in self.pilot.config['cameras'].items():
                try:s=await self.pilot.state(key,context);value=s.state
                except PilotRefusal:value='Unavailable'
                rows.append(binding['name']+' · '+value)
            rows.append('Automatic alerts have not been enabled.')
        elif action=='briefing':
            heading='Your day at home';body=now.strftime('%A, %B %d')+' · Family, Home and Reminders'
            for key in ('family','home'):
                s=await self.source(key,context)
                async with asyncio.timeout(10):
                    result=await self.hass.services.async_call('calendar','get_events',{'entity_id':s.entity_id,'start_date_time':now.isoformat(),'end_date_time':(now+timedelta(hours=24)).isoformat()},blocking=True,return_response=True,context=context)
                events=result.get(s.entity_id,{}).get('events',[])
                if not events:rows.append(self.config['sources'][key]['name']+' · No events in the next 24 hours')
                for event in events[:2]:
                    start=event.get('start','');stamp=dt_util.parse_datetime(start)
                    when=dt_util.as_local(stamp).strftime('%I:%M %p') if stamp else 'All day'
                    rows.append(when+' · '+str(event.get('summary','Untitled event')))
            s=await self.source('reminders',context)
            async with asyncio.timeout(10):
                result=await self.hass.services.async_call('todo','get_items',{'entity_id':s.entity_id,'status':['needs_action']},blocking=True,return_response=True,context=context)
            items=result.get(s.entity_id,{}).get('items',[])
            rows.extend('Reminder · '+str(i.get('summary','Untitled reminder')) for i in items[:2])
            if not items:rows.append('Reminders · Nothing outstanding')
            rows.append('Weather is not configured. More details are in Home Assistant.')
        return dict(kind=action,heading=heading[:100],body=body[:1200],rows=[r[:160] for r in rows[:8]],timezone=self.hass.config.time_zone)

    async def handle(self,call):
        action=call.data['action'];camera=call.data.get('camera')
        if action not in ACTIONS or camera is not None and action not in {'camera_popup','camera_large'}:raise ServiceValidationError('Choose one available screen action.')
        async with self.lock:
            try:
                await self.authorize(call.context,visible=action!='clear')
                if action.startswith('camera_') and camera not in self.pilot.config['cameras']:raise PilotRefusal('Choose a reviewed camera.')
                await self.cancel()
                if action=='clear':return await self.clear()
                key=str(uuid4());duration=call.data.get('duration',30)
                if not 10<=duration<=(7200 if action in {'art','art_next'} else 300):raise PilotRefusal('Choose a duration supported by this screen.')
                expires=time.time()+duration
                if action in {'art','art_next'}:
                    status=await self.read();index=0
                    if action=='art_next':
                        if status.get('view')!='art' or not status.get('visible'):raise PilotRefusal('Open artwork first.')
                        index=(status.get('art_index',0)+1)%len(ART_NAMES);expires=status['expires_at']
                    message=self.envelope(key,'show',expires,kind='art',heading='Quiet artwork',body='',rows=[],timezone=self.hass.config.time_zone,art_index=index)
                elif action.startswith('camera_'):
                    mode='popup' if action=='camera_popup' else 'large'
                    message=await self.camera_message(key,expires,0,camera,mode,call.context)
                else:message=self.envelope(key,'show',expires,**await self.card(action,call.context))
                await self.send(message);status=await self.wait_render(key)
                self.active=key
                if action.startswith('camera_'):
                    self.task=self.hass.async_create_task(self.camera_loop(key,expires,camera,mode,call.context),'Bedroom camera image preview')
                return {'rendered':True,'view':status['view'],'frames':status.get('frames',0),'physical_visibility':'owner_confirmation_required'}
            except ERRORS as error:
                # Never replay a timed-out presentation command.
                raise ServiceValidationError(str(error) if isinstance(error,PilotRefusal) else 'The TV screen request could not be confirmed. Check the screen before retrying.') from None


async def setup_screen(hass,entry):
    path=Path(hass.config.path(DOMAIN,'bedroom_screen.json'))
    if not path.exists():return
    if path.is_symlink() or path.stat().st_size>16384:raise ValueError('Invalid screen configuration')
    config=await hass.async_add_executor_job(lambda:json.loads(path.read_text()))
    if config.get('version')!=1 or set(config.get('sources',{}))!={'family','home','reminders','bedside'}:raise ValueError('Unexpected manual screen scope')
    for key,binding in config['sources'].items():
        domain={'family':'calendar','home':'calendar','reminders':'todo','bedside':'light'}[key]
        if not isinstance(binding,dict) or not {'entity_id','device_id','config_entry_id','name','area_id','unique_id','platform'}<=binding.keys() or not binding['entity_id'].startswith(domain+'.'):raise ValueError('Incomplete reviewed screen source')
    pilot=hass.data[DOMAIN]['pilot'];remote=hass.data[DOMAIN]['media_remote']
    controller=BedroomScreen(hass,pilot,remote,config);hass.data[DOMAIN]['bedroom_screen']=controller
    schema=probatio.Schema({probatio.Required('action'):probatio.In(ACTIONS),probatio.Optional('camera'):probatio.In(list(pilot.config['cameras'])),probatio.Optional('duration',default=30):probatio.All(int,probatio.Range(min=10,max=7200))})
    hass.services.async_register(DOMAIN,'bedroom_screen',controller.handle,schema=schema,supports_response=SupportsResponse.OPTIONAL)
    entry.async_on_unload(lambda:hass.services.async_remove(DOMAIN,'bedroom_screen'))
    entry.async_on_unload(async_track_time_interval(hass,controller.poll,timedelta(seconds=5)))
    entry.async_on_unload(lambda:hass.async_create_task(controller.cancel(),'Stop bedroom camera preview'))
    entry.async_on_unload(lambda:hass.data[DOMAIN].pop('bedroom_screen',None))
    await controller.poll()
