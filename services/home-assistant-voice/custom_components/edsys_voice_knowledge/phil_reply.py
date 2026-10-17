"""Relay only this native pipeline's TTS result to the reviewed Pi endpoint."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import subprocess
import time
import wave

import probatio
from homeassistant.components import tts,websocket_api
from homeassistant.components.assist_pipeline.pipeline import async_get_pipeline
from homeassistant.components.assist_pipeline.websocket_api import websocket_run as native_run

from . import DOMAIN
from .bedroom_screen import ERRORS
from .pilot_policy import PilotRefusal
from .screen_protocol import REPLY_CHUNK,REPLY_INFO_LABEL,MAX_REPLY,reply_player_id,reply_video_playing,reply_rejected_before_handoff


def native_ready():
    return callable(getattr(native_run,'_ws_schema',None)) and callable(getattr(native_run,'__wrapped__',None))


def pcm_wave(data):
    """Decode bounded native-provider audio in private pipes, never a URL/file."""
    if not isinstance(data,bytes) or not 0<len(data)<=4_000_000:raise ValueError('TTS result size')
    result=subprocess.run(['/usr/bin/ffmpeg','-hide_banner','-loglevel','error','-protocol_whitelist','pipe',
        '-i','pipe:0','-map_metadata','-1','-vn','-ar','24000','-ac','1','-t','61','-f','s16le','-c:a','pcm_s16le','pipe:1'],
        input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,check=True)
    raw=result.stdout
    if not 0<len(raw)<=MAX_REPLY-44 or len(raw)%2:raise ValueError('TTS duration/PCM size')
    output=io.BytesIO()
    with wave.open(output,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(raw)
    return output.getvalue()


class PiReplies:
    def __init__(self,controller):self.controller=controller

    async def preflight(self):
        """Read only player type/speed before selecting a Pi speech lease."""
        screen=self.controller.screen
        player=reply_player_id(await screen.rpc('Player.GetActivePlayers'))
        if player is not None:
            reply_video_playing(await screen.rpc('Player.GetProperties',playerid=player,properties=['speed']))

    def check(self,lease):
        if self.controller.active is not lease or lease.get('interrupted'):raise PilotRefusal('Voice session changed.')

    async def read(self,lease):
        data=await self.controller.screen.rpc('XBMC.GetInfoLabels',labels=[REPLY_INFO_LABEL])
        raw=data.get(REPLY_INFO_LABEL,'')
        if not isinstance(raw,str) or not 1<=len(raw)<=2048:raise PilotRefusal('Pi reply player is unavailable.')
        status=json.loads(raw)
        if status.get('version')!=1:raise PilotRefusal('Pi reply version needs review.')
        if status.get('request_id')!=lease['id'] or status.get('audio_ref')!=lease['audio_ref']:return None
        return status

    async def send(self,lease,command,**data):
        self.check(lease)
        await self.controller.screen.authorize(lease['context'])
        lease['reply_sequence']=lease.get('reply_sequence',-1)+1
        message=self.controller.screen.envelope(lease['id'],'reply_'+command,time.time()+7,
            lease['reply_sequence'],audio_ref=lease['audio_ref'],**data)
        await self.controller.screen.send(message)
        async with asyncio.timeout(4):
            while True:
                self.check(lease);status=await self.read(lease)
                self.check(lease)
                if status and status.get('sequence')==lease['reply_sequence']:
                    if status.get('state')=='rejected':
                        if command=='commit' and reply_rejected_before_handoff(status,lease['id'],lease['audio_ref'],lease['reply_sequence'],lease.get('reply_sha256')):
                            return status
                        raise PilotRefusal('Pi reply rejection is not proof of an unused sink.')
                    if status.get('state') in {'failed','cancelled','interrupted'}:raise PilotRefusal('Pi reply rejected.')
                    if command=='prepare' and (status.get('state')!='receiving' or status.get('received')!=0):raise PilotRefusal('Pi preparation uncertain.')
                    if command=='chunk' and status.get('received')!=data['offset']+len(base64.b64decode(data['data'])):raise PilotRefusal('Pi chunk uncertain.')
                    if command=='release' and status.get('state')!='released':raise PilotRefusal('Pi release uncertain.')
                    return status
                await asyncio.sleep(.04)

    def event(self,lease,event):
        if self.controller.active is not lease:return
        typ=str(event.type);data=event.data or {}
        if typ=='run-start':
            lease['stream_token']=data.get('tts_output',{}).get('token')
        elif typ=='tts-end' and not lease.get('reply_task'):
            token=data.get('tts_output',{}).get('token')
            if not token or token!=lease.get('stream_token'):return
            lease['audio_state']='preparing'
            lease['reply_task']=self.controller.screen.hass.async_create_background_task(self.relay(lease),'Phil HDMI reply')

    async def relay(self,lease):
        c=self.controller
        try:
            self.check(lease)
            stream=tts.async_get_stream(c.screen.hass,lease['stream_token'])
            if stream is None or stream.engine!=lease.get('tts_engine'):raise PilotRefusal('Reply stream expired or changed.')
            parts=[];size=0
            async with asyncio.timeout(45):
                async for part in stream.async_stream_result():
                    self.check(lease);size+=len(part)
                    if size>4_000_000:raise ValueError('Reply stream too large')
                    parts.append(part)
            wav=await c.screen.hass.async_add_executor_job(pcm_wave,b''.join(parts));parts.clear()
            lease['reply_sha256']=hashlib.sha256(wav).hexdigest()
            await self.send(lease,'prepare',size=len(wav),sha256=lease['reply_sha256'])
            for offset in range(0,len(wav),REPLY_CHUNK):
                await self.send(lease,'chunk',offset=offset,data=base64.b64encode(wav[offset:offset+REPLY_CHUNK]).decode())
            lease['commit_attempted']=True
            status=await self.send(lease,'commit')
            if reply_rejected_before_handoff(status,lease['id'],lease['audio_ref'],lease.get('reply_sequence'),lease['reply_sha256']):
                # This is the same already-generated reply, never a second
                # pipeline/household command. No other post-commit failure can
                # grant fallback, and a cancelled/replaced lease cannot do so.
                async with c.lock:
                    self.check(lease)
                    lease.update(audio_state='failed',fallback_allowed=True)
                    await c.finish()
                return
            async with asyncio.timeout(70):
                while True:
                    self.check(lease)
                    if status:
                        lease['audio_state']=status.get('state','uncertain')
                        lease['started']=bool(status.get('started'))
                        if status.get('state')=='finished':break
                        if status.get('state') in {'failed','cancelled','interrupted'}:raise PilotRefusal('Pi speech interrupted.')
                    await asyncio.sleep(.1);status=await self.read(lease)
            # This fresh HA gate and the Pi's independent one-use lease are both
            # required. Any manual service/native-player change forfeits resume.
            self.check(lease)
            status=await self.send(lease,'release',resume=True)
            lease.update(audio_state='finished',resume_result=status.get('resume_result'),fallback_allowed=False)
            async with c.lock:
                if c.active is lease:await c.finish('idle')
        except asyncio.CancelledError:raise
        except ERRORS + (subprocess.SubprocessError,):
            lease.update(audio_state='uncertain' if lease.get('commit_attempted') else 'failed',fallback_allowed=not lease.get('commit_attempted'))
            async with c.lock:
                if c.active is lease:await c.finish()


class PipelineConnection:
    """Delegate native binary audio, subscriptions and actions without copying Core."""
    def __init__(self,connection,replies,lease):self.connection,self.replies,self.lease=connection,replies,lease
    def __getattr__(self,name):return getattr(self.connection,name)
    def send_event(self,subscription,event):
        self.replies.event(self.lease,event)
        self.connection.send_event(subscription,event)


@websocket_api.websocket_command(probatio.All(probatio.Schema({
    probatio.Required('type'):'edsys_voice_knowledge/phil_pipeline',
    probatio.Required('phil_session_id'):str},extra=probatio.ALLOW_EXTRA)))
@websocket_api.async_response
async def websocket_pipeline(hass,connection,msg):
    c=hass.data.get(DOMAIN,{}).get('phil_voice');lease=c.active if c else None
    if not lease or lease['output']!='pi' or lease['id']!=msg['phil_session_id'] or lease['user']!=connection.user.id or lease.get('pipeline_bound') or not native_ready():
        connection.send_error(msg['id'],'voice_session_expired','Start a fresh voice turn; do not replay.');return
    native={k:v for k,v in msg.items() if k!='phil_session_id'};native['type']='assist_pipeline/run'
    try:native=native_run._ws_schema(native)
    except probatio.Invalid:
        connection.send_error(msg['id'],'invalid_format','Native voice request rejected.');return
    if native.get('pipeline')!=c.pipeline or native.get('device_id')!=c.screen.pilot.nimo_device_id or native.get('start_stage')!='stt' or native.get('end_stage')!='tts':
        connection.send_error(msg['id'],'wrong_voice_target','Use the reviewed Nimo pipeline.');return
    async with c.lock:
        if c.active is not lease or lease.get('pipeline_bound'):
            connection.send_error(msg['id'],'voice_session_expired','Start a fresh turn; do not replay.');return
        try:
            await c.screen.authorize(lease['context'])
            lease['tts_engine']=async_get_pipeline(hass,c.pipeline).tts_engine
        except ERRORS:
            await c.finish();connection.send_error(msg['id'],'voice_session_expired','Reply endpoint changed; do not replay.');return
        lease['pipeline_bound']=True
    await native_run.__wrapped__(hass,PipelineConnection(connection,c.replies,lease),native)


@websocket_api.websocket_command({probatio.Required('type'):'edsys_voice_knowledge/phil_status',probatio.Required('session_id'):str})
@websocket_api.async_response
async def websocket_status(hass,connection,msg):
    c=hass.data.get(DOMAIN,{}).get('phil_voice')
    result=c.status(connection.user.id,msg['session_id']) if c else {'active':False,'audio_state':'cancelled','fallback_allowed':False}
    connection.send_result(msg['id'],result)
