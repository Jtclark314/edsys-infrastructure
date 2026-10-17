"""Bounded manual dashboard messages over the existing Kodi connection.

This is not the proposed automatic voice presentation-event contract. No URL,
executable command or credential is accepted. Bounded PCM replies require a
separate active voice owner and one-use playback lease.
"""
import base64
import json
import math
import os
import re
import time
from uuid import UUID

SENDER = 'edsys.homeassistant'
MESSAGE = 'BedroomScreen'
PROPERTY = 'edsys.bedroom.screen'
INFO_LABEL = 'Window(Home).Property('+PROPERTY+')'
# Kodi's HTTP JSON-RPC body is limited to 65,536 bytes. Allow both the JPEG's
# base64 and the encrypted envelope's base64, plus JSON/RPC overhead.
MAX_JPEG = 32_000
MAX_MESSAGE = 46_000
MAX_WIRE = 62_000
AAD = b'edsys.bedroom.manual-screen.v1'
KINDS = {'butler', 'clock', 'dark', 'briefing', 'household', 'media', 'connections','art'}
ART_NAMES = ('dunes','orbit','hills','tide')
VOICE_PROPERTY = 'edsys.bedroom.voice'
VOICE_INFO_LABEL = 'Window(Home).Property('+VOICE_PROPERTY+')'
VOICE_STATES = {'listening','processing','responding','idle','failed','cancelled'}
REPLY_PROPERTY = 'edsys.bedroom.reply'
REPLY_INFO_LABEL = 'Window(Home).Property('+REPLY_PROPERTY+')'
MAX_REPLY = 2_880_044
REPLY_CHUNK = 32_768


def reply_player_id(players):
    """Only idle or one explicitly internal video player can lend the sink."""
    if not isinstance(players,list):raise ValueError('Unknown foreground player')
    if not players:return None
    if len(players)!=1 or not isinstance(players[0],dict):raise ValueError('Unsupported foreground player')
    player=players[0]
    if type(player.get('playerid')) is not int or player['playerid']!=1 or player.get('type')!='video' or player.get('playertype')!='internal':
        raise ValueError('Unsupported foreground player')
    return 1


def reply_video_playing(properties):
    """Unknown speeds fail closed on both the HA preflight and Pi commit."""
    speed=properties.get('speed') if isinstance(properties,dict) else None
    if type(speed) is not int or speed not in (0,1):raise ValueError('Unsupported playback speed')
    return speed==1


def reply_rejected_before_handoff(status,request_id,audio_ref,sequence,digest):
    """An authenticated, exact commit rejection is proof; a timeout is not."""
    return (isinstance(status,dict) and type(status.get('version')) is int and status['version']==1
            and status.get('request_id')==request_id and status.get('audio_ref')==audio_ref
            and type(sequence) is int and type(status.get('sequence')) is int and status['sequence']==sequence
            and isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest) is not None
            and status.get('sha256')==digest and status.get('state')=='rejected'
            and status.get('reason')=='player_state_unsupported'
            and status.get('started') is False and status.get('paused_by_us') is False
            and status.get('handoff_attempted') is False)


def seal(message,key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw=json.dumps(message,separators=(',',':'),ensure_ascii=True).encode()
    if len(raw)>MAX_MESSAGE:raise ValueError('Screen message too large')
    nonce=os.urandom(12)
    data=base64.b64encode(nonce+AESGCM(key).encrypt(nonce,raw,AAD)).decode('ascii')
    wire={'version':1,'sealed':data}
    if len(json.dumps(wire))>MAX_WIRE:raise ValueError('Screen envelope too large')
    return wire


def unseal(wire,key):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        if isinstance(wire,str):
            if len(wire)>MAX_WIRE:raise ValueError('Screen envelope too large')
            wire=json.loads(wire)
        if not isinstance(wire,dict) or set(wire)!={'version','sealed'} or wire['version']!=1:raise ValueError('Invalid encrypted screen envelope')
        data=wire['sealed']
        if not isinstance(data,str) or len(data)>MAX_WIRE:raise ValueError('Invalid encrypted screen data')
        raw=base64.b64decode(data,validate=True)
        message=AESGCM(key).decrypt(raw[:12],raw[12:],AAD)
        if len(message)>MAX_MESSAGE:raise ValueError('Screen message too large')
        return validate(json.loads(message))
    except (InvalidTag,TypeError,KeyError) as error:raise ValueError('Invalid encrypted screen message') from error


def text(value, limit):
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c!='\n' for c in value):
        raise ValueError('Invalid screen text')
    return value


def validate(data, now=None):
    now=time.time() if now is None else now
    if isinstance(data,str):
        if len(data)>MAX_MESSAGE:raise ValueError('Screen message too large')
        data=json.loads(data)
    if not isinstance(data,dict):raise ValueError('Invalid screen message')
    common={'version','request_id','sequence','issued_at','expires_at','command'}
    UUID(data.get('request_id',''))
    if data.get('version')!=1 or type(data.get('sequence')) is not int or not 0<=data['sequence']<=600:raise ValueError('Invalid screen version or sequence')
    for key in ['issued_at','expires_at']:
        n=data.get(key)
        if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n):raise ValueError('Invalid screen time')
    ttl=7201 if data.get('command')=='show' and data.get('kind')=='art' else 301
    if not now-30<=data['issued_at']<=now+5 or not now<data['expires_at']<=data['issued_at']+ttl:raise ValueError('Stale screen message')
    command=data.get('command')
    if not isinstance(command,str):raise ValueError('Invalid command')
    if command.startswith('reply_'):
        allowed=common|{'audio_ref'}
        UUID(data.get('audio_ref',''))
        if data['expires_at']>data['issued_at']+8:raise ValueError('Reply message too long lived')
        if command=='reply_prepare':
            allowed|={'size','sha256'}
            if type(data.get('size')) is not int or not 44<data['size']<=MAX_REPLY or not isinstance(data.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',data['sha256']):raise ValueError('Invalid bounded reply')
        elif command=='reply_chunk':
            allowed|={'offset','data'}
            if type(data.get('offset')) is not int or not 0<=data['offset']<MAX_REPLY or not isinstance(data.get('data'),str) or len(data['data'])>REPLY_CHUNK*4//3+4:raise ValueError('Invalid reply chunk')
            raw=base64.b64decode(data['data'],validate=True)
            if not 1<=len(raw)<=REPLY_CHUNK:raise ValueError('Invalid reply chunk size')
        elif command=='reply_release':
            allowed.add('resume')
            if type(data.get('resume')) is not bool:raise ValueError('Invalid reply release')
        elif command not in {'reply_commit','reply_cancel'}:raise ValueError('Unknown reply command')
    elif command=='voice':
        allowed=common|{'state','audio_playing'}
        if 'output' in data:
            allowed.add('output')
            if data['output'] not in {'nimo','pi'}:raise ValueError('Unknown voice output')
        if data.get('state') not in VOICE_STATES or type(data.get('audio_playing')) is not bool:raise ValueError('Invalid voice state')
        if data['audio_playing'] and data['state']!='responding':raise ValueError('Uncorrelated speaking')
        if data['expires_at']>data['issued_at']+8:raise ValueError('Voice heartbeat too long')
    elif command=='clear':allowed=common
    elif command=='show':
        allowed=common|{'kind','heading','body','rows','timezone'}
        if data.get('kind') not in KINDS:raise ValueError('Unsupported screen view')
        if data['kind']=='art':
            allowed.add('art_index')
            if type(data.get('art_index')) is not int or not 0<=data['art_index']<len(ART_NAMES):raise ValueError('Unknown artwork')
        text(data.get('heading'),100);text(data.get('body'),1200);text(data.get('timezone'),80)
        rows=data.get('rows')
        if not isinstance(rows,list) or len(rows)>8:raise ValueError('Too many screen rows')
        for row in rows:text(row,160)
    elif command=='camera_frame':
        allowed=common|{'label','mode','jpeg'}
        text(data.get('label'),80)
        if data.get('mode') not in {'popup','large'}:raise ValueError('Invalid preview size')
        if not isinstance(data.get('jpeg'),str) or len(data['jpeg'])>MAX_JPEG*4//3+4:raise ValueError('Invalid camera frame')
        raw=base64.b64decode(data['jpeg'],validate=True)
        if not 100<=len(raw)<=MAX_JPEG or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):raise ValueError('Invalid JPEG')
    else:raise ValueError('Unknown screen command')
    if set(data)!=allowed:raise ValueError('Unexpected screen fields')
    return data


class SequenceGuard:
    """A replaced/dismissed view can never return through a late camera frame."""
    def __init__(self):
        self.current=None;self.sequence=-1;self.retired={}

    def accept(self,data,now=None):
        now=time.time() if now is None else now
        validate(data,now)
        self.retired={k:v for k,v in self.retired.items() if v>now}
        key=data['request_id']
        if key in self.retired:return False
        if key!=self.current:
            if self.current:self.retired[self.current]=now+330
            self.current,self.sequence=key,-1
            if len(self.retired)>512:raise ValueError('Screen command rate exceeded')
        if data['sequence']<=self.sequence:return False
        self.sequence=data['sequence']
        return True

    def retire(self):
        if self.current:self.retired[self.current]=time.time()+330
        self.current=None;self.sequence=-1
