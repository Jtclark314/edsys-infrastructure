"""Authenticated Nimo lifecycle with an optional correlated HDMI reply lease."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4

import probatio
from homeassistant.components import websocket_api
from homeassistant.core import Context

from . import DOMAIN
from .bedroom_screen import ERRORS
from .pilot_policy import PilotRefusal
from .screen_protocol import VOICE_INFO_LABEL

PHASES=('listening','processing','audio_start','audio_waiting','audio_end','failed','cancelled','heartbeat')


class PhilVoice:
    def __init__(self,screen,pipeline,output='nimo'):
        self.screen,self.pipeline=screen,pipeline
        self.active=None;self.lock=asyncio.Lock()
        self.output=output;self.outcomes={}
        from .phil_reply import PiReplies
        self.replies=PiReplies(self)

    def status(self,user,session):
        lease=self.active if self.active and self.active['id']==session else self.outcomes.get(session)
        if not lease or lease['user']!=user:return {'active':False,'audio_state':'cancelled','fallback_allowed':False}
        return dict(active=self.active is lease,audio_state=lease.get('audio_state','pending'),
                    started=lease.get('started',False),fallback_allowed=lease.get('fallback_allowed',False),resume_result=lease.get('resume_result'))

    async def read(self):
        data=await self.screen.rpc('XBMC.GetInfoLabels',labels=[VOICE_INFO_LABEL])
        raw=data.get(VOICE_INFO_LABEL,'')
        if not isinstance(raw,str) or not 1<=len(raw)<=2048:raise PilotRefusal('Phil add-on is not ready.')
        status=json.loads(raw)
        if status.get('version')!=1:raise PilotRefusal('Phil version needs review.')
        return status

    async def emit(self,lease,state,audio=False):
        lease['wire_sequence']+=1
        now=time.time()
        message=self.screen.envelope(lease['id'],'voice',now+7,lease['wire_sequence'],state=state,audio_playing=audio,output=lease['output'])
        await self.screen.send(message)
        async with asyncio.timeout(3):
            while True:
                status=await self.read()
                if status.get('request_id')==lease['id'] and status.get('sequence')==lease['wire_sequence']:
                    if status.get('state') in {'dismissed','expired','offline'}:raise PilotRefusal('Phil was dismissed or expired.')
                    return status
                await asyncio.sleep(.1)

    async def finish(self,reason='cancelled'):
        lease,self.active=self.active,None
        if not lease:return
        task=lease.get('reply_task')
        if task and task is not asyncio.current_task():task.cancel()
        if lease['output']=='pi':
            if lease.get('audio_state') not in {'finished','failed','uncertain'}:lease['audio_state']='cancelled'
            lease.setdefault('fallback_allowed',False)
            self.outcomes[lease['id']]={k:lease[k] for k in ['id','user','audio_state','fallback_allowed','started'] if k in lease}
            self.outcomes[lease['id']]['resume_result']=lease.get('resume_result')
            if len(self.outcomes)>16:self.outcomes.pop(next(iter(self.outcomes)))
        # Clear only this lease; a late cancellation cannot dismiss a new turn.
        try:await self.emit(lease,reason)
        except ERRORS:pass

    async def begin(self,user_id,pipeline):
        async with self.lock:
            if pipeline!=self.pipeline:return {'active':False}
            from .phil_reply import native_ready
            context=Context(user_id=user_id)
            try:
                await self.screen.authorize(context)
                if self.active:await self.finish()
                output=self.output
                if output=='pi':
                    if not native_ready():output='nimo'
                    else:
                        try:await self.replies.preflight()
                        except ERRORS:output='nimo'
                    # Playback can change during those reads, as can the TV or
                    # caller's permission. A Nimo lease still needs this gate.
                    await self.screen.authorize(context)
                lease={'id':str(uuid4()),'audio_ref':str(uuid4()),'user':user_id,'context':context,
                       'sequence':-1,'wire_sequence':-1,'state':'listening','audio':False,'started':False,
                       'created':time.monotonic(),'seen':time.monotonic(),'output':output}
                await self.emit(lease,'listening');self.active=lease
                return {'active':True,'session_id':lease['id'],'audio_ref':lease['audio_ref'],'output':lease['output']}
            except ERRORS:
                return {'active':False}

    async def update(self,user_id,session,sequence,phase,audio_ref):
        async with self.lock:
            lease=self.active
            if not lease or (lease['user'],lease['id'])!=(user_id,session):return {'active':False}
            if type(sequence) is not int or not 0<=sequence<=600 or sequence<=lease['sequence'] or phase not in PHASES:
                raise PilotRefusal('Stale or unsupported Phil event.')
            if phase.startswith('audio_') and audio_ref!=lease['audio_ref']:
                raise PilotRefusal('Uncorrelated reply audio.')
            if not phase.startswith('audio_') and audio_ref is not None:raise PilotRefusal('Unexpected audio reference.')
            if time.monotonic()-lease['created']>=180 or time.monotonic()-lease['seen']>=6:
                await self.finish();return {'active':False}
            lease['sequence']=sequence;lease['seen']=time.monotonic()
            try:
                await self.screen.authorize(lease['context'])
                status=await self.read()
                if status.get('request_id')!=lease['id'] or not status.get('visible'):
                    await self.finish();return {'active':False}
                if lease['output']=='pi' and phase.startswith('audio_'):raise PilotRefusal('Pi owns reply playback evidence.')
                if lease['output']=='pi' and phase=='failed':
                    lease.update(audio_state='failed',fallback_allowed=not lease.get('commit_attempted'));await self.finish();return {'active':False}
                if phase in ('cancelled','audio_end'):
                    if phase=='audio_end' and not lease['started']:raise PilotRefusal('Audio did not start.')
                    await self.finish('idle' if phase=='audio_end' else 'cancelled');return {'active':False}
                if phase=='audio_start':lease.update(state='responding',audio=True,started=True)
                elif phase=='audio_waiting':lease.update(state='processing',audio=False)
                elif phase=='failed':lease.update(state='failed',audio=False)
                elif phase in ('processing','listening') and not lease['audio']:
                    lease['state']=phase
                result=await self.emit(lease,lease['state'],lease['audio'])
                return {'active':True,'state':result['state'],'rendered':bool(result.get('visible'))}
            except ERRORS:
                await self.finish();return {'active':False}

    async def watch(self):
        try:
            while True:
                await asyncio.sleep(2)
                async with self.lock:
                    lease=self.active
                    if not lease:continue
                    if time.monotonic()-lease['seen']>=6 or time.monotonic()-lease['created']>=180:
                        await self.finish();continue
                    try:
                        await self.screen.authorize(lease['context'])
                        status=await self.read()
                        if status.get('request_id')!=lease['id'] or not status.get('visible'):await self.finish()
                    except ERRORS:await self.finish()
        finally:
            async with self.lock:await self.finish()


@websocket_api.websocket_command({probatio.Required('type'):'edsys_voice_knowledge/phil_begin',
                                  probatio.Required('pipeline_id'):str})
@websocket_api.async_response
async def websocket_begin(hass,connection,msg):
    controller=hass.data.get(DOMAIN,{}).get('phil_voice')
    result=await controller.begin(connection.user.id,msg['pipeline_id']) if controller else {'active':False}
    connection.send_result(msg['id'],result)


@websocket_api.websocket_command({probatio.Required('type'):'edsys_voice_knowledge/phil_update',
    probatio.Required('session_id'):str,probatio.Required('sequence'):probatio.All(int,probatio.Range(min=0,max=600)),
    probatio.Required('phase'):probatio.In(PHASES),probatio.Required('audio_ref'):probatio.Any(None,str)})
@websocket_api.async_response
async def websocket_update(hass,connection,msg):
    controller=hass.data.get(DOMAIN,{}).get('phil_voice')
    try:
        result=await controller.update(connection.user.id,msg['session_id'],msg['sequence'],msg['phase'],msg['audio_ref']) if controller else {'active':False}
        connection.send_result(msg['id'],result)
    except PilotRefusal:connection.send_error(msg['id'],'stale_voice_event','Phil event rejected; do not replay it.')


async def setup_phil(hass,entry):
    # Optional private binding: the existing native named pipeline only.
    path=Path(hass.config.path(DOMAIN,'phil_voice.json'))
    websocket_api.async_register_command(hass,websocket_begin)
    websocket_api.async_register_command(hass,websocket_update)
    from .phil_reply import websocket_pipeline,websocket_status
    websocket_api.async_register_command(hass,websocket_pipeline)
    websocket_api.async_register_command(hass,websocket_status)
    if not path.exists():return
    if path.is_symlink() or path.stat().st_size>2048:raise ValueError('Invalid Phil binding')
    config=await hass.async_add_executor_job(lambda:json.loads(path.read_text()))
    if set(config)!={'version','pipeline_id','output'} or config['version']!=1 or config['output'] not in {'nimo','pi'} or not isinstance(config['pipeline_id'],str):
        raise ValueError('Unexpected Phil voice scope')
    screen=hass.data[DOMAIN].get('bedroom_screen')
    if not screen:return
    controller=PhilVoice(screen,config['pipeline_id'],config['output']);hass.data[DOMAIN]['phil_voice']=controller
    def service_changed(event):
        lease=controller.active
        if not lease or not lease.get('commit_attempted'):return
        data=event.data;domain=data.get('domain');service=data.get('service');details=data.get('service_data',{})
        targets=details.get('entity_id',[])
        if isinstance(targets,str):targets=[targets]
        known={b['entity_id'] for b in screen.remote.config['targets'].values()}
        if domain in {'kodi','webostv','media_player'} and (not targets or known.intersection(targets)) and service not in {'volume_up','volume_down','volume_set','volume_mute'}:
            lease['interrupted']=True
            async def retire():
                async with controller.lock:
                    if controller.active is lease:await controller.finish()
            hass.async_create_background_task(retire(),'Stop interrupted Phil speech')
    entry.async_on_unload(hass.bus.async_listen('call_service',service_changed))
    task=hass.async_create_background_task(controller.watch(),'Nimo Phil display lifetime')
    entry.async_on_unload(task.cancel)
    entry.async_on_unload(lambda:hass.data[DOMAIN].pop('phil_voice',None))
