"""Bounded, local-only Phil frame playback; no player, audio or HA authority."""
import math
from pathlib import Path
import time

STATES=('idle','listening','thinking','speaking','greeting','error')
FPS=24
FRAMES=96
VERSION='dimensional-phil-v2'


def animation_files():
    return {f'assets/phil/{state}-{frame:03}.png' for state in STATES for frame in range(FRAMES)}


def pose_for_view(state,audio_playing=False):
    if state=='responding' and audio_playing:return 'speaking'
    return {'listening':'listening','processing':'thinking','clarification':'listening',
            'failed':'error','unavailable':'error','complete':'greeting'}.get(state,'idle')


def frame_path(root,state,elapsed):
    if state not in STATES or type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:
        raise ValueError('Invalid local animation')
    return Path(root)/f'assets/phil/{state}-{int(elapsed*FPS)%FRAMES:03}.png'


class Character:
    def __init__(self,image,root,pose='idle',greeting=False):
        if pose not in STATES:raise ValueError('Unknown Phil pose')
        self.image,self.root,self.pose=image,root,pose
        self.started=time.monotonic();self.greeting=greeting;self.last=None;self.updates=0

    @property
    def status(self):
        return {'character':VERSION,'character_pose':self.pose,'character_frame':int(self.last.stem.rsplit('-',1)[1]) if self.last else 0,
                'character_updates':self.updates}

    def tick(self,now=None):
        now=time.monotonic() if now is None else now
        if self.greeting and now-self.started>=FRAMES/FPS:
            self.pose='idle';self.started=now;self.greeting=False
        path=frame_path(self.root,self.pose,max(0,now-self.started))
        if path==self.last:return False
        # Reuse Kodi's local texture cache. No per-frame disk writes, image
        # decoding subprocess, second video player, browser or network request.
        self.image.setImage(str(path));self.last=path;self.updates+=1
        return True
