#!/usr/bin/env python3
"""Atomic current bedroom recovery stage. Never controls voice or playback."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import zipfile

from common import Archive, MAX_FILE, MAX_TOTAL, logical_digest, read_stable, sha, sqlite_capture

ROOT = Path(__file__).resolve().parent
STAGE = Path('/srv/edsys-backup/staging/bedroom-recovery')
STATUS = Path('/var/lib/edsys-backup/bedroom-recovery.json')
FRESH_SECONDS = 26 * 3600
NIMO_NAMES = {'camera-view.js','start-assist.js','stop-assist.js','lg-key.js','lg-remote.json','settings.json','NimoVoice.exe','NimoVoice.cs','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll'}
PI_DATABASES = {'Addons33.db','Epg16.db','MyMusic83.db','MyVideos131.db','TV46.db','Textures13.db','ViewModes6.db'}
ENDPOINT_REASONS = frozenset({
    'endpoint_input_bound','endpoint_output_bound','endpoint_error_bound',
    'endpoint_deadline','endpoint_ssh_failed','endpoint_capture_failed',
    'endpoint_timeout','endpoint_capture_or_validation_failed','invalid_archive',
    'manifest_kind','manifest_source','manifest_time','manifest_members',
    'manifest_hash','manifest_missing','nimo_scope','nimo_missing_pins',
    'nimo_code_drift','zip_scope','zip_member','zip_size','zip_path',
    'archive_total','validation_deadline','unclassified_prior_failure',
})


def endpoint_failure_reason(error):
    """Publish fixed reason codes only, never endpoint stderr or exception text."""
    reason=error.args[0] if len(error.args)==1 else None
    if isinstance(reason,str) and reason in ENDPOINT_REASONS:
        return reason
    if isinstance(error,(TimeoutError,subprocess.TimeoutExpired)):
        return 'endpoint_timeout'
    if isinstance(error,(zipfile.BadZipFile,tarfile.TarError)):
        return 'invalid_archive'
    return 'endpoint_capture_or_validation_failed'


def status_failure_reason(endpoint):
    reason=endpoint.get('reason')
    return reason if isinstance(reason,str) and reason in ENDPOINT_REASONS else 'unclassified_prior_failure'


def secure_dir(path):
    path=Path(path)
    for component in [*reversed(path.parents),path]:
        if component.is_symlink():raise ValueError('symlink_directory')
    if not path.exists():path.mkdir(mode=0o700,parents=True)
    s=path.stat()
    if not path.is_dir() or s.st_uid!=os.geteuid() or s.st_mode & 0o077:
        raise ValueError('private_directory_permissions')


def atomic_json(path, value):
    if path.is_symlink():raise ValueError('symlink_status')
    fd,name=tempfile.mkstemp(prefix='.publish-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        os.chmod(name,0o600);os.replace(name,path)
        fsync_directory(path.parent)
    finally:Path(name).unlink(missing_ok=True)


def fsync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def run_file(args, destination, *, payload=None, limit=MAX_TOTAL+2*1024*1024, timeout=150):
    """Drain both pipes under size/time bounds; never expose endpoint stderr."""
    if payload is not None and len(payload)>256*1024:raise ValueError('endpoint_input_bound')
    deadline=time.monotonic()+timeout
    proc=subprocess.Popen(args,stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    selector=selectors.DefaultSelector()
    selector.register(proc.stdout,selectors.EVENT_READ,'output')
    selector.register(proc.stderr,selectors.EVENT_READ,'error')
    remaining=memoryview(payload) if payload is not None else None
    if remaining is not None:
        os.set_blocking(proc.stdin.fileno(),False)
        selector.register(proc.stdin,selectors.EVENT_WRITE,'input')
    size=0;errors=0
    try:
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as target:
            while selector.get_map():
                if time.monotonic()>deadline:raise TimeoutError('endpoint_deadline')
                for event,_ in selector.select(min(1,max(.01,deadline-time.monotonic()))):
                    if event.data=='input':
                        try:written=os.write(proc.stdin.fileno(),remaining[:8192])
                        except BlockingIOError:continue
                        remaining=remaining[written:]
                        if not remaining:
                            selector.unregister(proc.stdin);proc.stdin.close()
                        continue
                    chunk=os.read(event.fileobj.fileno(),65536)
                    if not chunk:selector.unregister(event.fileobj);continue
                    if event.data=='output':
                        size+=len(chunk)
                        if size>limit:raise ValueError('endpoint_output_bound')
                        target.write(chunk)
                    else:
                        errors+=len(chunk)
                        if errors>65536:raise ValueError('endpoint_error_bound')
            target.flush();os.fsync(target.fileno())
        returncode=proc.wait(timeout=max(.1,deadline-time.monotonic()))
        if returncode!=0:
            # OpenSSH uses 255 for SSH-layer failure. It does not identify
            # sleep, routing, authentication or host-key failure individually.
            reason='endpoint_ssh_failed' if returncode==255 and '/usr/bin/ssh' in args else 'endpoint_capture_failed'
            raise ValueError(reason)
    finally:
        selector.close()
        if proc.poll() is None:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
        if proc.stdin and not proc.stdin.closed:proc.stdin.close()
        proc.stdout.close();proc.stderr.close()


def ssh(host, command):
    if host not in {'edsys-edge-livingroom','nimo-laptop'}:raise ValueError('ssh_target')
    return ['/usr/sbin/runuser','-u','jeremy','--','/usr/bin/ssh','-F','/home/jeremy/.ssh/config',
      '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no',
      '-o','ClearAllForwardings=yes','-o','ConnectTimeout=5',host,command]


def check_database(raw, metadata, deadline=None):
    deadline=deadline or time.monotonic()+20
    db=sqlite3.connect(':memory:')
    try:
        db.deserialize(raw);db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('database_integrity')
        digest=logical_digest(db,deadline)
        if metadata.get('logical_sha256')!=digest or metadata.get('source_logical_sha256')!=digest or metadata.get('source_snapshot_parity') is not True:
            raise ValueError('database_parity')
    finally:db.close()


def check_manifest(manifest, members, kind, expected=None, *, fresh=True, deadline=None):
    if manifest.get('version')!=1 or manifest.get('kind')!=kind:raise ValueError('manifest_kind')
    source={'pi':'edsys-edge-livingroom','nimo':'NIMO-LAPTOP','abs':'audiobookshelf'}[kind]
    if manifest.get('source')!=source:raise ValueError('manifest_source')
    created=datetime.fromisoformat(manifest['created_at'].replace('Z','+00:00'))
    age=(datetime.now(timezone.utc)-created).total_seconds()
    if age < -60 or fresh and age>600:raise ValueError('manifest_time')
    declared=manifest.get('files',{})
    if set(members)!=set(declared)|{'manifest.json'}:raise ValueError('manifest_members')
    for name,data in declared.items():
        if deadline is not None and time.monotonic()>deadline:raise TimeoutError('validation_deadline')
        raw=members[name]
        if len(raw)!=data.get('bytes') or sha(raw)!=data.get('sha256'):raise ValueError('manifest_hash')
        if 'sqlite' in data:check_database(raw,data['sqlite'],deadline)
    if kind=='pi':
        from pi_worker import REQUIRED,OPTIONAL
        addon={n.removeprefix('addon/'):sha(v) for n,v in members.items() if n.startswith('addon/')}
        if addon!=expected or len(addon)!=602 or manifest.get('addon_version')!='0.7.1':raise ValueError('pi_reviewed_source')
        databases={n.removeprefix('databases/') for n in declared if n.startswith('databases/')}
        if databases!=PI_DATABASES or any('sqlite' not in declared['databases/'+n] for n in PI_DATABASES):raise ValueError('pi_databases')
        required={'config/etc/edsys-bedroom-kodi-control.json','config/var/lib/edsys-voice/bedroom-screen-key','config/etc/nftables.conf'}
        if not required<=set(declared):raise ValueError('pi_private_bindings')
        required_config={'config/'+n.lstrip('/') for n in REQUIRED}
        allowed_config=required_config|{'config/'+n.lstrip('/') for n in OPTIONAL}
        if not required_config<=set(declared) or not set(declared)<=(allowed_config|{'addon/'+n for n in expected}|{'databases/'+n for n in PI_DATABASES}):
            raise ValueError('pi_config_scope')
        check_media(manifest['media_before'],manifest['media_after'])
    elif kind=='nimo':
        if set(declared)!=NIMO_NAMES or manifest.get('browser_profile_included') is not False:raise ValueError('nimo_scope')
        if not isinstance(expected,dict) or set(expected)!=NIMO_NAMES:raise ValueError('nimo_missing_pins')
        for n,h in expected.items():
            if n in NIMO_NAMES and n not in {'settings.json','lg-remote.json'} and sha(members[n])!=h:raise ValueError('nimo_code_drift')
    elif kind=='abs':
        if 'databases/audiobookshelf.sqlite' not in declared or 'sqlite' not in declared['databases/audiobookshelf.sqlite']:
            raise ValueError('abs_database')


def validate_archive(path, kind, expected=None, *, fresh=True):
    deadline=time.monotonic()+30
    if kind=='pi':
        from pi_worker import REQUIRED,OPTIONAL
        allowed={'manifest.json'}|{'addon/'+n for n in expected}|{'databases/'+n for n in PI_DATABASES}|{'config/'+n.lstrip('/') for n in REQUIRED+OPTIONAL}
    elif kind=='abs':allowed={'manifest.json','databases/audiobookshelf.sqlite','config/docker-compose.yml','config/compose.env'}
    else:allowed=NIMO_NAMES|{'manifest.json'}
    members={};total=0
    if kind=='nimo':
        with zipfile.ZipFile(path) as archive:
            entries=archive.infolist()
            if len(entries)!=len(NIMO_NAMES)+1 or {e.filename for e in entries}!=NIMO_NAMES|{'manifest.json'}:raise ValueError('zip_scope')
            seen=set()
            for e in entries:
                if e.filename in seen or e.is_dir() or e.flag_bits&1 or stat.S_ISLNK(e.external_attr>>16):raise ValueError('zip_member')
                seen.add(e.filename)
                if e.file_size>8*1024*1024 or e.compress_size>8*1024*1024 or e.filename=='manifest.json' and e.file_size>1024*1024:raise ValueError('zip_size')
                if e.filename.startswith('/') or '\\' in e.filename or '..' in Path(e.filename).parts:raise ValueError('zip_path')
                total+=e.file_size
                if total>MAX_TOTAL:raise ValueError('archive_total')
            # ZipFile.read verifies CRC after all header/aggregate bounds passed.
            for e in entries:
                if time.monotonic()>deadline:raise TimeoutError('validation_deadline')
                members[e.filename]=archive.read(e)
    else:
        with tarfile.open(path,'r:') as archive:
            for e in archive:
                if time.monotonic()>deadline:raise TimeoutError('validation_deadline')
                if len(members)>=1000:raise ValueError('archive_member_count')
                if not e.isfile() or e.name in members or e.size>MAX_FILE:raise ValueError('tar_member')
                if e.name not in allowed or e.name=='manifest.json' and e.size>1024*1024:raise ValueError('tar_scope')
                if e.name.startswith('/') or '..' in Path(e.name).parts or '\\' in e.name:raise ValueError('tar_path')
                total+=e.size
                if total>MAX_TOTAL:raise ValueError('archive_total')
                members[e.name]=archive.extractfile(e).read()
    if 'manifest.json' not in members or len(members['manifest.json'])>1024*1024:raise ValueError('manifest_missing')
    manifest=json.loads(members['manifest.json'])
    check_manifest(manifest,members,kind,expected,fresh=fresh,deadline=deadline)
    return manifest


def capture_pi(destination, expected):
    import shlex
    code=(ROOT/'common.py').read_text()+'\n'+(ROOT/'pi_worker.py').read_text().replace('from common import Archive, read_stable, regular, sqlite_capture, sha\n','')
    run_file(ssh('edsys-edge-livingroom',shlex.join(['sudo','-n','python3','-c',code])),destination,
             payload=json.dumps(expected).encode(),timeout=150)


def capture_nimo(destination, expected):
    code=(ROOT/'nimo_worker.ps1').read_text()
    command='powershell.exe -NoProfile -NonInteractive -EncodedCommand '+base64.b64encode(code.encode('utf-16le')).decode()
    transfer=destination.with_suffix('.b64')
    try:
        run_file(ssh('nimo-laptop',command),transfer,limit=16*1024*1024,timeout=45)
        raw=base64.b64decode(transfer.read_bytes().strip(),validate=True)
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    finally:transfer.unlink(missing_ok=True)


def capture_abs(destination, expected_id):
    result=subprocess.run(['/usr/bin/docker','inspect','audiobookshelf','--format','{{json .Mounts}}'],capture_output=True,text=True,timeout=10,check=True)
    mounts=json.loads(result.stdout)
    wanted={'/config':'/srv/ssd1/docker/appdata/audiobookshelf/config','/metadata':'/srv/ssd1/docker/appdata/audiobookshelf/metadata','/audiobooks':'/mnt/media/audiobooks'}
    if {m['Destination']:m['Source'] for m in mounts}!=wanted or any(m['Type']!='bind' for m in mounts):raise ValueError('abs_mount_identity')
    info=subprocess.run(['/usr/bin/docker','inspect','audiobookshelf','--format','{{.Config.Image}}'],capture_output=True,text=True,timeout=10,check=True).stdout.strip()
    image_id=subprocess.run(['/usr/bin/docker','inspect','audiobookshelf','--format','{{.Image}}'],capture_output=True,text=True,timeout=10,check=True).stdout.strip()
    if info!='ghcr.io/advplyr/audiobookshelf:latest' or image_id!=expected_id:raise ValueError('abs_reviewed_image')
    raw,meta=sqlite_capture(Path(wanted['/config'])/'absdatabase.sqlite')
    with destination.open('xb') as stream:
        os.chmod(destination,0o600);a=Archive(stream)
        a.add('databases/audiobookshelf.sqlite',raw,sqlite=meta)
        compose=Path('/srv/ssd1/docker/stacks/audiobookshelf/docker-compose.yml')
        a.add('config/docker-compose.yml',read_stable(compose))
        env=compose.with_name('.env')
        if env.exists() or env.is_symlink():a.add('config/compose.env',read_stable(env))
        a.finish({'version':1,'kind':'abs','source':'audiobookshelf','image':info,'image_id':image_id,
                  'created_at':datetime.now(timezone.utc).isoformat(),'media_included':False,
                  'metadata_cache_included':False,'sqlite_databases':1})
        stream.flush();os.fsync(stream.fileno())


def check_media(before,after):
    if before.get('kodi_pid')!=after.get('kodi_pid'):raise ValueError('kodi_restarted')
    a=before.get('players');b=after.get('players')
    if not isinstance(a,list) or not isinstance(b,list) or len(a)!=len(b):raise ValueError('player_changed')
    for x,y in zip(a,b):
        if any(x.get(k)!=y.get(k) for k in ['playerid','type','item_sha256','speed']):raise ValueError('media_changed')
        if x.get('speed')==0 and x.get('position')!=y.get('position'):raise ValueError('paused_position_changed')


def stage(root=STAGE, status_path=STATUS, captures=None):
    secure_dir(root)
    incoming=root/'.incoming';generations=root/'generations'
    secure_dir(incoming);secure_dir(generations)
    current=root/'current.json'
    previous=json.loads(read_stable(current)) if current.exists() else None
    old_status={}
    if status_path.exists():
        try:old_status=json.loads(read_stable(status_path,1024*1024))
        except Exception:pass
    # Persist before any endpoint work. SIGKILL/outer timeout cannot leave a
    # prior successful attempt looking current; last-good data stays separate.
    atomic_json(status_path,{'status':'collecting','checked_at':datetime.now(timezone.utc).isoformat(),
      'last_success':previous.get('created_at') if previous else None,
      'generation':previous.get('generation') if previous else None,'endpoints':old_status.get('endpoints',{})})
    generation=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    pending=incoming/generation;secure_dir(pending)
    try:
        expected=json.loads(read_stable(ROOT/'expected.json')) if captures is None else {}
        collectors=captures or {'pi':lambda p:capture_pi(p,expected['pi']),
          'nimo':lambda p:capture_nimo(p,expected['nimo']),'abs':lambda p:capture_abs(p,expected['abs_image_id'])}
        manifests={};files={}
        outcomes={};fresh_count=0
        for kind in ['pi','nimo','abs']:
            name=kind+('.zip' if kind=='nimo' else '.tar')
            path=pending/name
            try:
                collectors[kind](path)
                m=validate_archive(path,kind,expected.get(kind)) if captures is None else collectors['validate'](path,kind)
                outcomes[kind]={'status':'fresh','captured_at':m['created_at']};fresh_count+=1
            except Exception as endpoint_error:
                path.unlink(missing_ok=True)
                m=None
                try:
                    if previous:
                        old_generation=previous.get('generation','')
                        if not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{8}',old_generation):raise ValueError('unsafe_previous_generation')
                        old=generations/old_generation/name
                        if old.is_file():
                            prior_bytes=read_stable(old,MAX_TOTAL+2*1024*1024)
                            with path.open('xb') as f:os.chmod(path,0o600);f.write(prior_bytes);f.flush();os.fsync(f.fileno())
                            m=validate_archive(path,kind,expected.get(kind),fresh=False) if captures is None else collectors['validate'](path,kind)
                except Exception:
                    path.unlink(missing_ok=True);m=None
                outcomes[kind]={'status':'stale' if m else 'missing','captured_at':m['created_at'] if m else None,
                                'error':type(endpoint_error).__name__,'reason':endpoint_failure_reason(endpoint_error)}
            if m is not None:
                manifests[kind]=m
                files[name]={'bytes':path.stat().st_size,'sha256':sha(path.read_bytes())}
        if not fresh_count:
            atomic_json(status_path,{'status':'degraded','checked_at':datetime.now(timezone.utc).isoformat(),
              'last_success':previous.get('created_at') if previous else None,
              'generation':previous.get('generation') if previous else None,'endpoints':outcomes,'reason':'no_fresh_endpoint'})
            return None
        manifest={'version':1,'created_at':datetime.now(timezone.utc).isoformat(),'generation':generation,
          'files':files,'sources':{k:{field:m.get(field) for field in ['source','created_at','addon_version','addon_files','owned_file_count','sqlite_databases','browser_profile_included']} for k,m in manifests.items()},
          'complete':True,'all_endpoints_fresh':fresh_count==3,'endpoints':outcomes,'services_restarted':False,'media_included':False}
        atomic_json(pending/'manifest.json',manifest)
        fsync_directory(pending)
        os.rename(pending,generations/generation)
        fsync_directory(generations)
        atomic_json(current,{'generation':generation,'created_at':manifest['created_at'],'previous_generation':previous.get('generation') if previous else None})
        atomic_json(status_path,{'status':'success' if fresh_count==3 else 'degraded','checked_at':manifest['created_at'],
          'last_success':manifest['created_at'],'generation':generation,'sources':manifest['sources'],'endpoints':outcomes})
        return manifest
    except Exception as e:
        last=previous.get('created_at') if previous else None
        atomic_json(status_path,{'status':'degraded','checked_at':datetime.now(timezone.utc).isoformat(),'last_success':last,
          'generation':previous.get('generation') if previous else None,'error':type(e).__name__,'reason':'endpoint_capture_or_validation_failed'})
        raise
    finally:
        if pending.exists():shutil.rmtree(pending)


def health(path=STATUS):
    try:
        s=json.loads(read_stable(path,1024*1024))
        last=datetime.fromisoformat(s['last_success'].replace('Z','+00:00'))
        age=(datetime.now(timezone.utc)-last).total_seconds()
        healthy=s.get('status')=='success' and 0<=age<=FRESH_SECONDS and set(s.get('endpoints',{}))=={'pi','nimo','abs'}
        endpoints={}
        for k,v in s.get('endpoints',{}).items():
            try:seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(v['captured_at'].replace('Z','+00:00'))).total_seconds()
            except Exception:seconds=None
            endpoints[k]={'status':v['status'],'captured_at':v.get('captured_at'),'age_seconds':round(seconds) if seconds is not None else None}
            if v.get('status')!='fresh':
                endpoints[k]['reason']=status_failure_reason(v)
            if v.get('status')!='fresh' or seconds is None or not 0<=seconds<=FRESH_SECONDS:healthy=False
        return {'bedroom_recovery_health':'healthy' if healthy else 'degraded','last_success':s.get('last_success'),
                'age_seconds':round(age),'generation':s.get('generation'),'latest_attempt':s.get('status'),'endpoints':endpoints}
    except Exception:
        return {'bedroom_recovery_health':'degraded','reason':'missing_or_invalid_current_checkpoint'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('operation',choices=['stage','status']);args=p.parse_args()
    if args.operation=='status':print(json.dumps(health()));return
    if os.geteuid()!=0:raise ValueError('root_required')
    os.umask(0o077)
    fd=os.open('/run/edsys-bedroom-recovery.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        m=stage()
        if m is None:
            print(json.dumps(health()));raise SystemExit(1)
        print(json.dumps({'status':'success' if m['all_endpoints_fresh'] else 'degraded','generation':m['generation'],'sources':m['sources'],'endpoints':m['endpoints']}))
        if not m['all_endpoints_fresh']:raise SystemExit(1)
    finally:os.close(fd)


if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'status':'degraded','reason':'bedroom_stage_failed','error':type(e).__name__}))
        raise SystemExit(1)
