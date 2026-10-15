"""Manual cards and refreshing camera images; never owns video/audio playback."""
import base64
from datetime import datetime
import json
from pathlib import Path
import queue
import subprocess
import time
from zoneinfo import ZoneInfo

import xbmc
import xbmcgui
from manual_protocol import SENDER,MESSAGE,PROPERTY,MAX_WIRE,ART_NAMES,SequenceGuard,validate
from model import kodi_text
from character import Character,frame_path

ROOT=Path(__file__).resolve().parent
RUNTIME=Path('/run/edsys-bedroom-display')


class ScreenMonitor(xbmc.Monitor):
    def __init__(self,jobs):
        super().__init__();self.jobs=jobs

    def onNotification(self,sender,method,data):
        if sender!=SENDER or method!='Other.'+MESSAGE:return
        try:
            if not isinstance(data,str) or len(data)>MAX_WIRE:raise ValueError('Oversized encrypted message')
            # Debian cryptography's Rust module cannot load inside Kodi's
            # subinterpreter. A fixed local helper decrypts via private pipes.
            result=subprocess.run(['/usr/bin/python3',str(ROOT/'decrypt_screen.py')],input=data,capture_output=True,text=True,timeout=2)
            if result.returncode:raise ValueError('Invalid encrypted message')
            message=validate(result.stdout)
            self.jobs.put_nowait(message)
        except (ValueError,TypeError,KeyError,queue.Full,subprocess.TimeoutExpired):pass


class ScreenWindow(xbmcgui.WindowDialog):
    def __init__(self,message):
        super().__init__();self.done=False;self.kind=message.get('kind','camera')
        w,h=self.getWidth(),self.getHeight()
        def rect(x,y,a,b):return [round(x*w/1920),round(y*h/1080),round(a*w/1920),round(b*h/1080)]
        self.clock=None;self.image=None;self.footer=None;self.files=[]
        self.art_index=None;self.character=None
        if self.kind=='dark':
            self.addControl(xbmcgui.ControlImage(0,0,w,h,str(ROOT/'assets/black.png')))
        elif self.kind=='art':
            self.art_index=message['art_index'];self.next_art=time.monotonic()+20
            self.image=xbmcgui.ControlImage(0,0,w,h,str(ROOT/'assets'/('art-'+ART_NAMES[self.art_index]+'.png')),aspectRatio=2)
            self.footer=xbmcgui.ControlLabel(*rect(80,995,1760,50),'Quiet artwork · Back to return · Changes every 20 seconds',font='font13',textColor='FFDDD9CB')
            self.addControls([self.image,self.footer]);self.hide_hint=time.monotonic()+5
        elif self.kind=='camera':
            x,y,cw,ch=(1140,120,720,560) if message['mode']=='popup' else (80,55,1760,970)
            self.addControl(xbmcgui.ControlImage(*rect(x,y,cw,ch),str(ROOT/'assets/panel.png')))
            self.addControl(xbmcgui.ControlLabel(*rect(x+24,y+14,cw-48,50),kodi_text(message['label']),font='font30',textColor='FFF4F0E7'))
            self.image=xbmcgui.ControlImage(*rect(x+20,y+75,cw-40,ch-150),'',aspectRatio=2)
            self.footer=xbmcgui.ControlLabel(*rect(x+24,y+ch-65,cw-48,45),'Updating images · Back to close',font='font13',textColor='FFCFDCD6')
            self.addControls([self.image,self.footer])
        elif self.kind=='clock':
            self.addControl(xbmcgui.ControlImage(0,0,w,h,str(ROOT/'assets/black.png')))
            self.clock=xbmcgui.ControlLabel(*rect(200,380,1520,150),'',font='font60',textColor='FFCFDCD6',alignment=2)
            self.date=xbmcgui.ControlLabel(*rect(200,540,1520,55),'',font='font30',textColor='FFCFDCD6',alignment=2)
            self.addControls([self.clock,self.date]);self.timezone=ZoneInfo(message['timezone'])
            self.addControl(xbmcgui.ControlLabel(*rect(350,600,1220,55),'Back to return to video',font='font13',textColor='FF7C8C91',alignment=2))
        else:
            self.addControl(xbmcgui.ControlImage(*rect(620,460,1180,570),str(ROOT/'assets/panel.png')))
            greeting=self.kind=='butler';pose='greeting' if greeting else 'idle'
            avatar=xbmcgui.ControlImage(*rect(120,390,470,627),str(frame_path(ROOT,pose,0)),aspectRatio=2)
            self.addControl(avatar);self.character=Character(avatar,ROOT,pose,greeting=greeting)
            self.addControl(xbmcgui.ControlLabel(*rect(670,485,1080,65),kodi_text(message['heading']),font='font30',textColor='FFF4F0E7'))
            body=xbmcgui.ControlTextBox(*rect(670,560,1050,405),font='font13',textColor='FFCFDCD6')
            self.addControl(body)
            body.setText(kodi_text('\n'.join([message['body'],*message['rows']])))

    def frame(self,message):
        path=RUNTIME/('screen-'+message['request_id']+'-'+str(message['sequence']%3)+'.jpg')
        path.write_bytes(base64.b64decode(message['jpeg'],validate=True));path.chmod(0o600)
        if path not in self.files:self.files.append(path)
        self.image.setImage(str(path),False)
        self.footer.setLabel('Updating images · Frame '+str(message['sequence']+1)+' · Back to close')

    def tick(self):
        if self.character:self.character.tick()
        if self.art_index is not None:
            now=time.monotonic()
            if now>=self.hide_hint:self.footer.setVisible(False)
            if now>=self.next_art:
                self.art_index=(self.art_index+1)%len(ART_NAMES);self.next_art=now+20
                self.image.setImage(str(ROOT/'assets'/('art-'+ART_NAMES[self.art_index]+'.png')),False)
        if self.clock:
            now=datetime.now(self.timezone)
            self.clock.setLabel(now.strftime('%I:%M %p'));self.date.setLabel(now.strftime('%A, %B %d'))

    def onAction(self,action):
        if action.getId() in (7,9,10,13,92):self.done=True

    def dispose(self):
        self.close()
        for path in self.files:path.unlink(missing_ok=True)


class ManualDisplay:
    def __init__(self):
        self.jobs=queue.Queue(maxsize=4);self.guard=SequenceGuard();self.window=None
        self.status={'version':1,'view':'idle','visible':False,'frames':0}
        self.expires=0;self.last_frame=0;self.published=0
        # Crash/restart discards prior private images, never replays a view.
        for path in RUNTIME.glob('screen-*.jpg'):path.unlink(missing_ok=True)
        self.publish()

    def publish(self):
        xbmcgui.Window(10000).setProperty(PROPERTY,json.dumps(self.status,separators=(',',':')))

    def close(self,reason='idle'):
        if self.window:self.window.dispose();self.window=None
        self.guard.retire()
        self.status.update(view=reason,visible=False)
        self.publish()

    def tick(self):
        now=time.time()
        if self.window:
            if self.window.done or now>=self.expires:self.close()
            elif self.status['view']=='camera' and now-self.last_frame>6:self.close('stale')
            else:
                self.window.tick()
                if self.window.character:
                    self.status.update(self.window.character.status);self.publish()
                if self.window.art_index is not None and self.status.get('art_index')!=self.window.art_index:
                    self.status['art_index']=self.window.art_index;self.publish()
        try:message=self.jobs.get_nowait()
        except queue.Empty:return
        try:
            if not self.guard.accept(message,now):return
            key=message['request_id'];command=message['command']
            if command=='clear':
                self.close();self.status['request_id']=key;self.publish();return
            new=self.status.get('request_id')!=key or self.window is None
            if new:
                if self.window:self.window.dispose()
                self.window=ScreenWindow(message);self.window.show()
                self.status={'version':1,'request_id':key,'view':message.get('kind','camera'),'visible':True,'frames':0}
                if message.get('kind')=='art':self.status.update(art_index=message['art_index'],expires_at=message['expires_at'])
            self.expires=message['expires_at']
            if command=='camera_frame':
                self.window.frame(message);self.last_frame=now
                self.status.update(frames=self.status['frames']+1,frame_at=now,label=message['label'],mode=message['mode'])
            self.publish()
        except (ValueError,TypeError,KeyError,RuntimeError,OSError):self.close('failed')

    def dispose(self):self.close('offline')
