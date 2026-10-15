"""Trusted local-only GUI qualification bridge. No HA/network subscription."""
from collections import OrderedDict
from dataclasses import replace
import json
import os
from pathlib import Path
import queue
import socket
import stat
import struct
import threading
import time

import xbmc
import xbmcgui

from default import Overlay
from local_control import KEYS, SOCKET, validate_request
from model import View
from manual_display import ManualDisplay, ScreenMonitor

ROOT=Path(__file__).resolve().parent
TEST=Path('/var/lib/edsys-voice/bedroom-qualification/sample-1080p-h264.mp4')


def serve(jobs, stop, scenes):
    path=Path(SOCKET)
    if path.exists() or path.is_symlink():
        s=path.lstat()
        if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=os.getuid():
            raise RuntimeError('unsafe socket path')
        path.unlink()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(SOCKET)
        os.chmod(SOCKET,0o600)
        server.listen(4)
        server.settimeout(0.2)
        try:
            while not stop.is_set():
                try:conn,_=server.accept()
                except socket.timeout:continue
                with conn:
                    conn.settimeout(2)
                    try:
                        _,uid,_=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                        if uid not in (0,os.getuid()):raise ValueError('peer')
                        data=bytearray()
                        while b'\n' not in data:
                            part=conn.recv(4096)
                            if not part or len(data)+len(part)>4096:raise ValueError('size')
                            data.extend(part)
                        command=validate_request(json.loads(data),scenes)
                        reply=queue.Queue(maxsize=1)
                        jobs.put_nowait((time.monotonic()+2,command,reply))
                        response=reply.get(timeout=3)
                    except (ValueError,TypeError,socket.timeout,queue.Full,queue.Empty):
                        response={'ok':False,'reason':'rejected_or_uncertain'}
                    try:conn.sendall(json.dumps(response).encode()+b'\n')
                    except OSError:pass
        finally:
            path.unlink(missing_ok=True)


def main():
    scenes={v['key']:View.from_dict(v) for v in json.loads((ROOT/'views.json').read_text(encoding='utf-8'))}
    jobs,stop=queue.Queue(maxsize=16),threading.Event()
    thread=threading.Thread(target=serve,args=(jobs,stop,scenes),daemon=True)
    thread.start()
    manual=ManualDisplay()
    monitor=ScreenMonitor(manual.jobs)
    overlay=None
    recent=OrderedDict()
    player=xbmc.Player()
    try:
        while not monitor.waitForAbort(0.05):
            manual.tick()
            if overlay and (overlay.done or time.monotonic()>=overlay.until):
                overlay.close();overlay=None
            if overlay:overlay.tick()
            try:deadline,command,reply=jobs.get_nowait()
            except queue.Empty:continue
            key,op=command['request_id'],command['op']
            if key in recent:
                reply.put(recent[key]);continue
            if time.monotonic()>deadline:
                reply.put({'ok':False,'reason':'expired'});continue
            response={'ok':True,'op':op}
            try:
                if op=='status':
                    playing=player.isPlaying()
                    plex=json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc':'2.0','id':1,'method':'Addons.GetAddonDetails','params':{'addonid':'script.plexmod','properties':['enabled','version']}})))
                    response.update(overlay=overlay.view.key if overlay else None,
                                    video_playing=player.isPlayingVideo(),
                                    test_playing=playing and player.getPlayingFile()==str(TEST),
                                    position_seconds=player.getTime() if playing else None,
                                    resolution=xbmc.getInfoLabel('System.ScreenResolution'),
                                    video_codec=xbmc.getInfoLabel('VideoPlayer.VideoCodec'),
                                    window_id=xbmcgui.getCurrentWindowId(),
                                    dialog_id=xbmcgui.getCurrentWindowDialogId(),
                                    overlay_seconds_remaining=round(max(0,overlay.until-time.monotonic()),1) if overlay else 0,
                                    plex=plex.get('result',{}).get('addon'))
                elif op in ('scene','dismiss'):
                    if overlay:overlay.close();overlay=None
                    if op=='scene':
                        view=scenes[command['scene']]
                        if 'duration_seconds' in command:view=replace(view,ttl_seconds=command['duration_seconds'])
                        overlay=Overlay(view);overlay.show()
                elif op=='key':
                    xbmc.executebuiltin('ActivateWindow(Home)' if command['key']=='home' else 'Action('+KEYS[command['key']]+')')
                elif op=='screenshot':
                    capture=Path('/run/edsys-bedroom-display')/('capture-'+key+'.png')
                    xbmc.executebuiltin('TakeScreenshot('+str(capture)+',false)')
                    response.update(capture_path=str(capture),capture_requested=True)
                elif op=='play_test':
                    if player.isPlaying() or not TEST.is_file():raise ValueError('busy_or_missing_test')
                    player.play(str(TEST))
                    response['playback_requested']=True
                elif op=='stop_test':
                    if not player.isPlaying() or player.getPlayingFile()!=str(TEST):raise ValueError('not_owned_test')
                    player.stop()
                elif op=='update_repos':
                    xbmc.executebuiltin('UpdateAddonRepos')
                elif op=='install_plex':
                    xbmc.executebuiltin('InstallAddon(script.plexmod)')
                elif op=='open_plex':
                    xbmc.executebuiltin('RunAddon(script.plexmod)')
            except (RuntimeError,ValueError):
                response={'ok':False,'reason':'not_ready_or_not_owned'}
            recent[key]=response
            while len(recent)>512:recent.popitem(last=False)
            reply.put(response)
    finally:
        manual.dispose()
        if overlay:overlay.close()
        stop.set()
        thread.join(timeout=3)


if __name__=='__main__':
    main()
