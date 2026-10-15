"""Ephemeral Phil animation metadata. No microphone, TTS or player control."""
import json
from pathlib import Path
import time

import xbmcgui
from character import Character,frame_path,pose_for_view
from manual_protocol import SequenceGuard,VOICE_PROPERTY

ROOT=Path(__file__).resolve().parent


class VoiceWindow(xbmcgui.WindowDialog):
    def __init__(self):
        super().__init__();self.done=False
        w,h=self.getWidth(),self.getHeight()
        def rect(x,y,a,b):return [round(x*w/1920),round(y*h/1080),round(a*w/1920),round(b*h/1080)]
        self.avatar=xbmcgui.ControlImage(*rect(120,390,470,627),str(frame_path(ROOT,'idle',0)),aspectRatio=2)
        self.label=xbmcgui.ControlLabel(*rect(155,350,410,45),'',font='font13',textColor='FFF4F0E7')
        self.addControls([self.avatar,self.label]);self.character=Character(self.avatar,ROOT)

    def state(self,state,audio_playing):
        pose=pose_for_view(state,audio_playing)
        if self.character.pose!=pose:
            self.character.pose=pose;self.character.started=time.monotonic();self.character.last=None
        self.label.setLabel({'listening':'Listening on Nimo','processing':'Thinking…',
                            'responding':'Speaking on Nimo' if audio_playing else 'Reply pending',
                            'failed':'Voice unavailable'}.get(state,''))

    def onAction(self,action):
        if action.getId() in (7,9,10,13,92):self.done=True


class VoiceDisplay:
    def __init__(self):
        self.guard=SequenceGuard();self.window=None;self.expires=0
        self.started=time.time()
        self.status={'version':1,'visible':False,'state':'idle'};self.publish()

    def publish(self):
        xbmcgui.Window(10000).setProperty(VOICE_PROPERTY,json.dumps(self.status,separators=(',',':')))

    def close(self,reason='idle'):
        if self.window:self.window.close();self.window=None
        self.guard.retire();self.status.update(visible=False,state=reason,audio_playing=False);self.publish()

    def receive(self,message):
        # Restart/reconnect never resurrects a heartbeat from an old turn.
        if message['issued_at']<self.started:return
        if message['request_id']!=self.guard.current and (message['sequence']!=0 or message['state']!='listening'):return
        if not self.guard.accept(message):return
        self.status.update(request_id=message['request_id'],sequence=message['sequence'])
        if message['state'] in ('idle','cancelled'):
            self.close(message['state']);return
        if not self.window:self.window=VoiceWindow();self.window.show()
        self.window.state(message['state'],message['audio_playing']);self.expires=message['expires_at']
        self.status.update(visible=True,state=message['state'],audio_playing=message['audio_playing'])
        self.window.character.tick();self.status.update(self.window.character.status);self.publish()

    def tick(self):
        if not self.window:return
        if self.window.done:self.close('dismissed')
        elif time.time()>=self.expires:self.close('expired')
        else:
            self.window.character.tick();self.status.update(self.window.character.status);self.publish()
