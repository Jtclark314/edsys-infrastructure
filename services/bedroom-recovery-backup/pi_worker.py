"""Fixed read-only Pi capture streamed to the existing hub SSH connection."""
from common import Archive, read_stable, regular, sqlite_capture, sha
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

ADDON = Path('/usr/share/kodi/addons/service.edsys.bedroom-display')
DATABASE = Path('/var/lib/edsys-voice/.kodi/userdata/Database')
DB_NAMES = {'Addons33.db','Epg16.db','MyMusic83.db','MyVideos131.db','TV46.db','Textures13.db','ViewModes6.db'}
REQUIRED = [
 '/etc/edsys-voice/audio.json', '/etc/edsys-bedroom-kodi-control.json',
 '/var/lib/edsys-voice/bedroom-screen-key', '/etc/nftables.conf',
 '/etc/systemd/system/edsys-bedroom-display.service',
 '/etc/systemd/user/edsys-voice.service', '/usr/local/lib/edsys-voice/run.py',
 '/boot/firmware/config.txt', '/boot/firmware/cmdline.txt',
 '/var/lib/edsys-voice/.kodi/userdata/guisettings.xml',
 '/var/lib/edsys-voice/.kodi/userdata/profiles.xml',
 '/var/lib/edsys-voice/.kodi/userdata/RssFeeds.xml',
 '/var/lib/edsys-voice/.kodi/userdata/addon_data/script.plexmod/settings.xml',
 '/var/lib/edsys-voice/.kodi/userdata/addon_data/skin.estuary/settings.xml',
]
OPTIONAL = ['/var/lib/edsys-voice/.kodi/userdata/' + n for n in
            ['sources.xml','passwords.xml','advancedsettings.xml','addon_data/plugin.video.youtube/settings.xml']]


def ownership(path):
    s=regular(Path(path)).stat()
    return {'source_mode':format(s.st_mode&0o7777,'04o'),'source_uid':s.st_uid,'source_gid':s.st_gid}


def player_state():
    c = json.loads(read_stable(Path('/etc/edsys-bedroom-kodi-control.json')))
    if c.get('port') != 8080:
        raise ValueError('kodi_port_changed')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('rpc_redirect_refused')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def rpc(method, params=None):
        request = urllib.request.Request('http://127.0.0.1:8080/jsonrpc',
          data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params or {}}).encode(),
          headers={'Content-Type':'application/json','Authorization':'Basic '+base64.b64encode((c['username']+':'+c['password']).encode()).decode()})
        with opener.open(request, timeout=5) as r:
            raw=r.read(65537)
        if len(raw)>65536:raise ValueError('rpc_bound')
        data=json.loads(raw)
        if 'error' in data:raise ValueError('rpc_read_failed')
        return data['result']
    rows=[]
    for player in rpc('Player.GetActivePlayers'):
        pid=player['playerid']
        p=rpc('Player.GetProperties',{'playerid':pid,'properties':['speed','time']})
        item=rpc('Player.GetItem',{'playerid':pid,'properties':['file']})['item']
        rows.append({'playerid':pid,'type':player.get('type'),'speed':p['speed'],
                     'position':p['time'],'item_sha256':sha(item.get('file','').encode())})
    return {'players':rows,'kodi_pid':subprocess.check_output(['pgrep','-x','kodi.bin'],text=True).strip()}


def main():
    expected=json.load(sys.stdin)
    if socket.gethostname()!='edsys-edge-livingroom':raise ValueError('pi_identity')
    before=player_state()
    settings_before=sha(read_stable(Path(REQUIRED[9])))
    names={str(p.relative_to(ADDON)) for p in ADDON.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if names!=set(expected) or len(names)!=602:raise ValueError('addon_scope_changed')
    if {p.name for p in DATABASE.glob('*.db')}!=DB_NAMES:raise ValueError('database_scope_changed')
    a=Archive(sys.stdout.buffer)
    for name in sorted(expected):
        raw=read_stable(ADDON/name)
        if sha(raw)!=expected[name]:raise ValueError('addon_drift')
        a.add('addon/'+name,raw,**ownership(ADDON/name))
    for name in REQUIRED:
        a.add('config/'+name.lstrip('/'),read_stable(Path(name)),**ownership(name))
    absent=[]
    for name in OPTIONAL:
        p=Path(name)
        if p.exists() or p.is_symlink():a.add('config/'+name.lstrip('/'),read_stable(p),**ownership(p))
        else:absent.append(name)
    for name in sorted(DB_NAMES):
        raw, meta=sqlite_capture(DATABASE/name)
        a.add('databases/'+name,raw,sqlite=meta,**ownership(DATABASE/name))
    after=player_state()
    if before['kodi_pid']!=after['kodi_pid'] or settings_before!=sha(read_stable(Path(REQUIRED[9]))):
        raise ValueError('pi_process_or_settings_changed')
    a.finish({'version':1,'kind':'pi','source':'edsys-edge-livingroom',
      'created_at':datetime.now(timezone.utc).isoformat(),'addon_version':'0.7.1',
      'addon_files':602,'sqlite_databases':7,'media_before':before,'media_after':after,
      'settings_unchanged':True,'optional_absent':absent,'media_included':False,
      'capture_method':'readonly files and online SQLite into memory; no service/media actions'})


if __name__=='__main__':
    try:main()
    except Exception:
        print('pi_capture_failed',file=sys.stderr)
        raise SystemExit(1)
