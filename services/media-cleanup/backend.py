"""Fixed EdSys ARR/Plex routes for the media-removal planner.

This module never accepts a caller-selected URL, credential path or command.
Credentials remain in their existing stores. Local file removal and journaling
are owned by engine.py; native ARR deletions are guarded here immediately
before dispatch. There are deliberately no automatic write retries.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


MAX_BYTES = 16 * 1024 * 1024
HTTP_TIMEOUT = 30
SSH_TIMEOUT = 120
PLEX_BASE = "http://127.0.0.1:32400"
PLEX_PREFERENCES = Path("/srv/plex-config/Library/Application Support/Plex Media Server/Preferences.xml")
MEDIA_ROOTS = {"movie": "/mnt/media/movies", "show": "/mnt/media/tv"}
PLEX_ROOTS = {"movie": "/media/movies", "show": "/media/tv"}
ARR_ROOTS = {"radarr": "/movies", "sonarr": "/tv"}
REF = re.compile(r"(movie:radarr|show:sonarr|movie:plex|show:plex):([1-9][0-9]{0,9})\Z")
MOVIE_VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".wmv", ".mpg", ".mpeg"}


class BackendError(RuntimeError):
    """Sanitized failure; an uncertain write must be inspected before retrying."""

    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _integer(value, label="identifier") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 10**10:
        raise BackendError(f"Invalid {label}")
    return value


def _root(path: str, kind: str, *, source: str = "local") -> str:
    roots = MEDIA_ROOTS if source == "local" else PLEX_ROOTS
    if kind not in roots or not isinstance(path, str) or "\x00" in path:
        raise BackendError("Unsupported media root mapping")
    parts = PurePosixPath(path).parts
    base = PurePosixPath(roots[kind])
    if len(parts) != len(base.parts) + 1 or parts[:-1] != base.parts or parts[-1] in (".", ".."):
        raise BackendError("Media must have its own direct library directory")
    # PurePath normalizes '.', so also reject noncanonical spellings.
    if str(PurePosixPath(path)) != path or any(p in (".", "..") for p in path.split("/")):
        raise BackendError("Noncanonical media root")
    return MEDIA_ROOTS[kind] + "/" + parts[-1]


def _part_root(path: str, kind: str) -> str:
    if not isinstance(path, str) or "\x00" in path:
        raise BackendError("Unsupported Plex media path")
    prefix = PLEX_ROOTS[kind] + "/"
    if not path.startswith(prefix) or str(PurePosixPath(path)) != path or any(p in (".", "..") for p in path.split("/")):
        raise BackendError("Plex media is outside the configured library")
    relative = path[len(prefix):].split("/")
    if len(relative) == 1 and kind == "movie":
        return _flat_movie_path(path, source="plex")
    if len(relative) < 2 or not relative[0]:
        raise BackendError("Plex media must be inside its own library directory")
    return _root(PLEX_ROOTS[kind] + "/" + relative[0], kind, source="plex")


def _flat_movie_path(path: str, *, source: str = "local") -> str:
    """A direct movie video is an exact-file target, never its library base."""
    local = _root(path, "movie", source=source)
    if PurePosixPath(local).suffix.lower() not in MOVIE_VIDEO_EXTENSIONS:
        raise BackendError("Standalone movie media must name one direct video file")
    return local


def _check_standalone_file(path: str) -> None:
    for component in [*reversed(Path(path).parents), Path(path)]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue  # The exact file may already be gone during readback.
        if stat.S_ISLNK(info.st_mode):
            raise BackendError("Standalone movie paths must not contain symlinks")
        if component == Path(path) and not stat.S_ISREG(info.st_mode):
            raise BackendError("Standalone movie media is not a regular file")


def _item(item: dict) -> dict:
    if not isinstance(item, dict) or not isinstance(item.get("ref"), str):
        raise BackendError("Invalid media selection")
    match = REF.fullmatch(item["ref"])
    if not match:
        raise BackendError("Invalid media reference")
    kind, service, identity = item["ref"].split(":")
    if item.get("kind") != kind or _root(item.get("root"), kind) != item["root"]:
        raise BackendError("Media selection identity or root changed")
    standalone = item.get("standalone_file", False)
    if type(standalone) is not bool:
        raise BackendError("Invalid standalone movie flag")
    if standalone:
        if kind != "movie" or service != "plex":
            raise BackendError("Only unmanaged Plex movies can use standalone files")
        _flat_movie_path(item["root"])
        _check_standalone_file(item["root"])
    if service != "plex":
        if item.get("arr_service") != service or item.get("arr_id") != int(identity):
            raise BackendError("Media selection identity changed")
        _integer(item.get("external_id"), "external identifier")
    elif item.get("arr_service") is not None or item.get("arr_id") is not None:
        raise BackendError("Unmanaged Plex selection cannot name an ARR record")
    keys = item.get("plex_keys", [])
    if not isinstance(keys, list) or len(keys) > 200 or any(not isinstance(k, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", k) for k in keys):
        raise BackendError("Invalid Plex record identifiers")
    if item.get("mapping_blocker"):
        raise BackendError("This media has an ambiguous library mapping; resolve it before removal")
    return item


def _display(value) -> str:
    return "".join(c for c in str(value or "") if ord(c) >= 32)[:400]


def _kind(value: str) -> str:
    if value not in {"movie", "show", "any"}:
        raise BackendError("kind must be movie, show or any")
    return value


# The only remote program dispatched over the existing SSH route. Its stdin is
# bounded JSON data; no request can add a URL, path, program or API operation.
REMOTE_HELPER = r'''
import json,os,stat,subprocess,sys,time,urllib.request,urllib.parse,urllib.error,xml.etree.ElementTree as ET
MAX=16*1024*1024
POST_DELETE_VERIFY_SECONDS=10.0
POST_DELETE_VERIFY_INTERVAL=0.25
PORTS={'radarr':7878,'sonarr':8989}
ROOTS={'radarr':'/movies','sonarr':'/tv'}
attempted=False
class Error(Exception):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
keys={}
container_pids={}
def integer(v):
 if isinstance(v,bool) or not isinstance(v,int) or not 0<v<10**10:raise Error('Invalid numeric identifier')
 return v
def api(service,path,method='GET',body=None,query=None,missing=False,timeout=30):
 global attempted
 if service not in PORTS:raise Error('Unsupported ARR service')
 if service not in keys:
  keys[service]=ET.parse('/opt/arr-vpn/'+service+'/config.xml').getroot().findtext('ApiKey')
  if not keys[service]:raise Error('Existing ARR credential unavailable')
 url='http://127.0.0.1:'+str(PORTS[service])+'/api/v3'+path
 if query:url+='?'+urllib.parse.urlencode(query)
 data=None if body is None else json.dumps(body).encode()
 headers={'X-Api-Key':keys[service],'Accept':'application/json'}
 if data is not None:headers['Content-Type']='application/json'
 if method!='GET':attempted=True
 try:
  with opener.open(urllib.request.Request(url,headers=headers,data=data,method=method),timeout=min(30,timeout)) as r:
   b=r.read(MAX+1)
   if len(b)>MAX:raise Error('ARR response exceeded the bounded limit')
   return r.status,json.loads(b) if b else None
 except urllib.error.HTTPError as e:
  if missing and e.code==404:return 404,None
  raise Error(service+' API returned HTTP '+str(e.code)) from None
def root(service,path):
 prefix=ROOTS[service]+'/'
 if not isinstance(path,str) or not path.startswith(prefix) or not path[len(prefix):] or '/' in path[len(prefix):] or path[len(prefix):] in ('.','..') or '\x00' in path:raise Error('Unsupported ARR library mapping')
 return path
def projection(service,x,collections):
 p=root(service,x.get('path'))
 stats=x.get('statistics') or {};collection=x.get('collection') or {};ct=collections.get(collection.get('tmdbId'),{})
 return {'kind':'movie' if service=='radarr' else 'show','arr_service':service,'arr_id':integer(x.get('id')),'external_id':integer(x.get('tmdbId') if service=='radarr' else x.get('tvdbId')),'title':x.get('title',''),'year':x.get('year'),'container_root':p,'collection_id':ct.get('id'),'collection_title':collection.get('title'),'estimated_bytes':x.get('sizeOnDisk',stats.get('sizeOnDisk',0)) or 0,'has_files':bool(x.get('hasFile') if service=='radarr' else stats.get('episodeFileCount',0))}
def validate_item(item):
 service=item.get('arr_service')
 if service not in PORTS:raise Error('Unsupported managed media service')
 identity=integer(item.get('arr_id'));external=integer(item.get('external_id'));p=root(service,item.get('container_root'))
 path=('/movie/' if service=='radarr' else '/series/')+str(identity)
 status,current=api(service,path,missing=True)
 if status==404:raise Error('ARR record is no longer present; create a new plan')
 field='tmdbId' if service=='radarr' else 'tvdbId'
 if current.get(field)!=external or current.get('path')!=p:raise Error('ARR record identity or root changed; create a new plan')
 return service,path,current
def container_path(service,p):
 if service not in container_pids:
  try:container_pids[service]=int(subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',service],stderr=subprocess.DEVNULL,timeout=10).strip())
  except Exception:raise Error('Cannot verify the existing ARR container mount') from None
 pid=container_pids[service]
 if pid<=0:raise Error('ARR container is not running')
 base='/proc/'+str(pid)+'/root'+ROOTS[service]
 os.stat(base)
 if os.path.islink(base):raise Error('ARR library base must not be a symlink')
 target='/proc/'+str(pid)+'/root'+p
 if os.path.lexists(target) and os.path.islink(target):raise Error('ARR media root must not be a symlink')
 return target
def file_set(target):
 if not os.path.lexists(target):return []
 if not os.path.isdir(target):raise Error('ARR media root is not a directory')
 found=[]
 def onerror(e):raise Error('Cannot enumerate the ARR media mount')
 for directory,dirs,files in os.walk(target,followlinks=False,onerror=onerror):
  for name in dirs:
   if os.path.islink(os.path.join(directory,name)):raise Error('ARR media directory contains a symlink')
  for name in files:
   p=os.path.join(directory,name);s=os.lstat(p)
   if not stat.S_ISREG(s.st_mode):raise Error('ARR media contains a nonregular file')
   found.append({'path':os.path.relpath(p,target).replace(os.sep,'/'),'size':s.st_size,'mtime_ns':s.st_mtime_ns})
   if len(found)>50000:raise Error('ARR file inventory exceeded its limit')
 return sorted(found,key=lambda x:x['path'])
def queue_refs(service):
 result=set()
 for page in range(1,102):
  q=api(service,'/queue',query={'page':page,'pageSize':100,'includeUnknownSeriesItems':'true','includeUnknownMovieItems':'true'})[1]
  records=q.get('records',[])
  for x in records:
   identity=x.get('movieId' if service=='radarr' else 'seriesId')
   if isinstance(identity,int) and identity>0:result.add(('movie:' if service=='radarr' else 'show:')+service+':'+str(identity))
  if page*100>=q.get('totalRecords',len(records)):return sorted(result)
 raise Error('ARR queue exceeded its bounded inventory')
def preflight(items,files=False):
 configs={};catalogs={};checked=[]
 for item in items:
  service,path,current=validate_item(item)
  if service not in configs:configs[service]=api(service,'/config/mediamanagement')[1]
  if service not in catalogs:catalogs[service]=api(service,'/movie' if service=='radarr' else '/series')[1]
  if configs[service].get('recycleBin'):raise Error('ARR recycle bin is enabled; this tool cannot promise immediate space recovery')
  for other in catalogs[service]:
   other_root=root(service,other.get('path'))
   # Unknown/nested mappings cannot be hidden from overlap detection. A new
   # overlapping record also blocks immediately before native recursive delete.
   if other.get('id')!=item['arr_id'] and (other_root==current['path'] or other_root.startswith(current['path']+'/') or current['path'].startswith(other_root+'/')):raise Error('Another ARR record overlaps the selected media root')
   container_path(service,other_root)
  target=container_path(service,current['path'])
  if os.path.exists(target):os.listdir(target)
  elif current.get('hasFile') or (current.get('statistics') or {}).get('episodeFileCount',0):raise Error('ARR file record has an inaccessible media root')
  if files:
   expected=item.get('relative_files')
   if not isinstance(expected,list) or len(expected)>50000:raise Error('The plan has no bounded file inventory')
   canonical=[]
   for row in expected:
    name=row.get('path')
    if not isinstance(name,str) or name.startswith('/') or any(x in ('','..','.') for x in name.split('/')) or '\x00' in name:raise Error('Invalid relative file in plan')
    if isinstance(row.get('size'),bool) or not isinstance(row.get('size'),int) or row['size']<0 or not isinstance(row.get('mtime_ns'),int):raise Error('Invalid planned file attributes')
    canonical.append({'path':name,'size':row['size'],'mtime_ns':row['mtime_ns']})
   if sorted(canonical,key=lambda x:x['path'])!=file_set(target):raise Error('ARR media files changed after preview; create a new plan')
  checked.append(item['ref'])
 return {'blockers':[],'checked':checked}
def verify_deleted(service,path,external):
 # Native ARR deletion events and import exclusions can settle asynchronously.
 # Dispatch DELETE exactly once, then poll GETs only within one shared budget.
 deadline=time.monotonic()+POST_DELETE_VERIFY_SECONDS
 gone=False
 while time.monotonic()<deadline:
  remaining=deadline-time.monotonic()
  if remaining<=0:break
  gone=api(service,path,missing=True,timeout=remaining)[0]==404
  remaining=deadline-time.monotonic()
  if gone and remaining>0:
   exclusions=api(service,'/exclusions' if service=='radarr' else '/importlistexclusion',timeout=remaining)[1]
   field='tmdbId' if service=='radarr' else 'tvdbId'
   if any(x.get(field)==external for x in exclusions):return
  remaining=deadline-time.monotonic()
  if remaining>0:time.sleep(min(POST_DELETE_VERIFY_INTERVAL,remaining))
 if gone:raise Error('ARR deletion was dispatched but its import exclusion was not verified within 10 seconds; inspect status before retrying')
 raise Error('ARR deletion was dispatched but its record disappearance was not verified within 10 seconds; inspect status before retrying')
def execute(req):
 operation=req.get('operation')
 if operation=='catalog':
  kind=req.get('kind','any')
  if kind not in ('movie','show','any'):raise Error('Unsupported media kind')
  collections={x['tmdbId']:x for x in api('radarr','/collection')[1]} if kind!='show' else {}
  result=[]
  for service,path in [('radarr','/movie'),('sonarr','/series')]:
   if kind=='movie' and service!='radarr' or kind=='show' and service!='sonarr':continue
   for item in api(service,path)[1]:
    result.append(projection(service,item,collections))
  return result
 if operation=='active_downloads':
  kind=req.get('kind','any')
  if kind not in ('movie','show','any'):raise Error('Unsupported media kind')
  return (queue_refs('radarr') if kind!='show' else [])+(queue_refs('sonarr') if kind!='movie' else [])
 if operation=='preflight':
  items=req.get('items',[])
  return preflight(items,files=all('relative_files' in item for item in items))
 if operation=='delete':
  items=req.get('items',[])
  if len(items)!=1:raise Error('Delete requires one exact managed selection')
  item=items[0];service,path,current=validate_item(item)
  if item['ref'] in queue_refs(service):raise Error('The selected media still has an ARR download or import queue entry')
  # Queue reads precede the last file-set check: no further read operation may
  # create a long gap between validating the private mount and native deletion.
  preflight(items,files=True)
  query={'deleteFiles':'true','addImportExclusion':'true'} if service=='radarr' else {'deleteFiles':'true','addImportListExclusion':'true'}
  status,_=api(service,path,method='DELETE',query=query)
  verify_deleted(service,path,item['external_id'])
  return {'ref':item['ref'],'http_status':status,'arr_removed':True,'import_exclusion_verified':True}
 if operation=='disable_collections':
  results=[]
  ids=req.get('ids',[])
  if not isinstance(ids,list) or len(ids)>100:raise Error('Invalid collection selection')
  for identity in ids:
   identity=integer(identity);path='/collection/'+str(identity);_,c=api('radarr',path)
   c['monitored']=False;c['searchOnAdd']=False;status,_=api('radarr',path,method='PUT',body=c)
   _,c=api('radarr',path)
   if c.get('monitored') is not False or c.get('searchOnAdd') is not False:raise Error('Collection settings could not be verified')
   results.append({'collection_id':identity,'monitored':False,'search_on_add':False,'http_status':status})
  return results
 if operation=='verify':
  items=req.get('items',[]);cache={};results=[]
  for item in items:
   service=item.get('arr_service');identity=integer(item.get('arr_id'));external=integer(item.get('external_id'))
   if service not in PORTS:raise Error('Unsupported managed media service')
   if service not in cache:
    records=api(service,'/movie' if service=='radarr' else '/series')[1];excluded=api(service,'/exclusions' if service=='radarr' else '/importlistexclusion')[1];field='tmdbId' if service=='radarr' else 'tvdbId'
    cache[service]=({x['id'] for x in records},{x[field] for x in excluded},{x[field] for x in records})
   ids,exclusions,remaining_external=cache[service];results.append({'ref':item['ref'],'arr_removed':identity not in ids and external not in remaining_external,'import_exclusion_verified':external in exclusions})
  return results
 raise Error('Unsupported bounded media operation')
try:
 raw=sys.stdin.buffer.read(MAX+1)
 if len(raw)>MAX:raise Error('Request exceeded bounded limit')
 request=json.loads(raw)
 if not isinstance(request,dict):raise Error('Invalid request')
 result=execute(request);output=json.dumps({'ok':True,'result':result})
 if len(output.encode())>MAX:raise Error('Response exceeded bounded limit')
 print(output,flush=True)
except Error as e:print(json.dumps({'ok':False,'error':str(e),'uncertain':attempted}),flush=True)
except Exception:print(json.dumps({'ok':False,'error':'Existing ARR route failed; private upstream details were suppressed','uncertain':attempted}),flush=True)
'''


def _remote_items(items: list[dict]) -> list[dict]:
    result = []
    for original in items:
        item = _item(original)
        service = item.get("arr_service")
        if not service:
            continue
        selected = {k: item[k] for k in ("ref", "arr_service", "arr_id", "external_id")}
        selected["container_root"] = ARR_ROOTS[service] + "/" + Path(item["root"]).name
        if "relative_files" in item:
            selected["relative_files"] = item["relative_files"]
        result.append(selected)
    return result


def _run_remote(request: dict):
    payload = json.dumps(request, ensure_ascii=True).encode()
    if len(payload) > MAX_BYTES:
        raise BackendError("Media adapter request exceeded its bounded limit")
    command = "sudo -n python3 -c " + shlex.quote(REMOTE_HELPER)
    process = None
    try:
        process = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10",
             "-o", "ServerAliveCountMax=2", "arr-server", command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        output = bytearray()
        written = 0
        deadline = time.monotonic() + SSH_TIMEOUT
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE, "in")
            selector.register(process.stdout, selectors.EVENT_READ, "out")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackendError("ARR route timed out; inspect status before retrying", uncertain=request["operation"] in ("delete", "disable_collections"))
                for key, _ in selector.select(min(remaining, 1)):
                    if key.data == "in":
                        count = os.write(key.fd, payload[written:written + 65536])
                        written += count
                        if written == len(payload):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(process.stdout)
                            process.stdout.close()
                        else:
                            output.extend(data)
                            if len(output) > MAX_BYTES:
                                raise BackendError("ARR route output exceeded its bounded limit", uncertain=request["operation"] in ("delete", "disable_collections"))
        returncode = process.wait(timeout=max(1, deadline - time.monotonic()))
        if returncode != 0:
            raise BackendError("Existing ARR SSH route failed; no alternate identity or retry attempted", uncertain=request["operation"] in ("delete", "disable_collections"))
        result = json.loads(output)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise BackendError(_display(result.get("error", "ARR operation failed")), uncertain=bool(result.get("uncertain")))
        return result["result"]
    except BackendError:
        raise
    except Exception:
        raise BackendError("Existing ARR route failed; private transport details were suppressed", uncertain=request["operation"] in ("delete", "disable_collections")) from None
    finally:
        if process:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout):
                if stream and not stream.closed:
                    stream.close()


class LiveBackend:
    """Credential-reusing live adapter. All methods use fixed destinations."""

    def __init__(self):
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _plex(self, path: str, *, method: str = "GET", missing: bool = False):
        if not path.startswith("/library/") and path != "/status/sessions":
            raise BackendError("Unsupported internal Plex route")
        try:
            token = ET.parse(PLEX_PREFERENCES).getroot().get("PlexOnlineToken")
            if not token:
                raise BackendError("Existing Plex credential is unavailable")
            request = urllib.request.Request(PLEX_BASE + path, headers={"X-Plex-Token": token, "Accept": "application/xml"}, method=method)
            with self._opener.open(request, timeout=HTTP_TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise BackendError("Plex response exceeded its bounded limit", uncertain=method != "GET")
                return response.status, ET.fromstring(body) if body.strip() else None
        except urllib.error.HTTPError as exc:
            if missing and exc.code == 404:
                return 404, None
            raise BackendError(f"Plex API returned HTTP {exc.code}; inspect state before retrying a write", uncertain=method != "GET") from None
        except BackendError:
            raise
        except Exception:
            raise BackendError("Existing Plex route failed; private upstream details were suppressed", uncertain=method != "GET") from None

    def _plex_descriptor(self, element: ET.Element, kind: str) -> dict:
        key = element.get("ratingKey", "")
        if not re.fullmatch(r"[1-9][0-9]{0,9}", key):
            raise BackendError("Plex returned an unsupported record identifier")
        standalone = False
        if kind == "show":
            _, detail = self._plex("/library/metadata/" + key)
            elements = detail.findall("Directory")
            if len(elements) != 1 or elements[0].get("type") != "show":
                raise BackendError("Plex show metadata changed")
            element = elements[0]
            roots = {_root(x.get("path"), kind, source="plex") for x in element.findall("Location")}
            size = 0
            has_files = int(element.get("leafCount", "0")) > 0
        else:
            parts = element.findall("./Media/Part")
            roots = {_part_root(x.get("file"), kind) for x in parts}
            standalone = any(PurePosixPath(x.get("file")).parent == PurePosixPath(PLEX_ROOTS["movie"]) for x in parts)
            size = sum(int(x.get("size", "0")) for x in parts)
            has_files = bool(parts)
        if not roots:
            raise BackendError("Plex record has no exact supported library directory")
        return {"key": key, "title": _display(element.get("title")), "year": int(element.get("year")) if element.get("year", "").isdigit() else None,
                "roots": sorted(roots), "estimated_bytes": size, "has_files": has_files,
                "standalone_file": standalone}

    def catalog(self, kind: str = "any") -> list[dict]:
        requested_kind = _kind(kind)
        raw = _run_remote({"operation": "catalog", "kind": requested_kind})
        result = []
        by_root = {}
        for x in raw:
            service = x["arr_service"]
            kind = x["kind"]
            if requested_kind != "any" and kind != requested_kind:
                continue
            prefix = ARR_ROOTS[service] + "/"
            if not x["container_root"].startswith(prefix):
                raise BackendError("ARR returned an unsupported media root")
            local = _root(MEDIA_ROOTS[kind] + "/" + x["container_root"][len(prefix):], kind)
            item = {k: x.get(k) for k in ("kind", "arr_service", "arr_id", "external_id", "year", "collection_id", "collection_title", "estimated_bytes", "has_files")}
            item.update(ref=kind + ":" + service + ":" + str(x["arr_id"]), title=_display(x["title"]), root=local, plex_keys=[], aliases=[])
            result.append(item)
            by_root.setdefault((kind, local), []).append(item)
        _, sections = self._plex("/library/sections")
        descriptors = []
        show_elements = []
        for section in sections.findall("Directory"):
            kind = "movie" if section.get("type") == "movie" else "show" if section.get("type") == "show" else None
            if not kind or (requested_kind != "any" and kind != requested_kind) or {x.get("path") for x in section.findall("Location")} != {PLEX_ROOTS[kind]}:
                continue
            key = section.get("key", "")
            if not key.isdigit():
                raise BackendError("Unsupported Plex library identifier")
            _, listing = self._plex("/library/sections/" + key + "/all?includeGuids=1")
            if kind == "show":
                show_elements.extend(listing.findall("Directory"))
            else:
                for element in listing.findall("Video"):
                    try:
                        descriptors.append((kind, self._plex_descriptor(element, kind)))
                    except BackendError:
                        # An unsupported Plex path must not silently turn a
                        # known ARR title into an apparently Plex-free item.
                        tmdb_ids = {g.get("id", "")[len("tmdb://"):] for g in element.findall("Guid") if g.get("id", "").startswith("tmdb://")}
                        for item in result:
                            if item["kind"] == "movie" and str(item["external_id"]) in tmdb_ids:
                                item["mapping_blocker"] = "Plex has an unsupported file mapping for this title"
                        continue
        if len(show_elements) > 2000:
            raise BackendError("Plex show catalogue exceeded its bounded limit")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for descriptor in pool.map(lambda x: self._plex_descriptor(x, "show"), show_elements):
                descriptors.append(("show", descriptor))
        for kind, d in descriptors:
            matches = [x for root in d["roots"] for x in by_root.get((kind, root), [])]
            blocker = "Plex record spans multiple library directories" if len(d["roots"]) != 1 else "Multiple ARR records share the library directory" if len(matches) > 1 else ""
            if matches:
                for item in matches:
                    item["plex_keys"].append(d["key"])
                    if d["title"] and d["title"] != item["title"]:
                        item["aliases"].append(d["title"])
                    if d["standalone_file"]:
                        item["mapping_blocker"] = "A standalone Plex file overlaps an ARR library directory"
                    if blocker:
                        item["mapping_blocker"] = blocker
            else:
                result.append({"ref": kind + ":plex:" + d["key"], "kind": kind, "title": d["title"], "year": d["year"], "root": d["roots"][0],
                               "arr_service": None, "arr_id": None, "external_id": None, "collection_id": None, "collection_title": None,
                               "plex_keys": [d["key"]], "estimated_bytes": d["estimated_bytes"], "has_files": d["has_files"], "mapping_blocker": blocker,
                               "aliases": [], **({"standalone_file": True} if d["standalone_file"] else {})})
        for item in result:
            item["plex_keys"] = sorted(set(item["plex_keys"]), key=int)
            item["aliases"] = sorted(set(item["aliases"]))
        return result

    def active_paths(self) -> list[str]:
        _, sessions = self._plex("/status/sessions")
        paths = []
        for video in sessions.findall("Video"):
            parts = video.findall("./Media/Part")
            if not parts or not all(p.get("file") for p in parts):
                key = video.get("ratingKey", "")
                if not re.fullmatch(r"[1-9][0-9]{0,9}", key):
                    raise BackendError("Cannot identify an active Plex session; playback was preserved")
                _, detail = self._plex("/library/metadata/" + key)
                parts = detail.findall(".//Media/Part")
                if not parts or not all(p.get("file") for p in parts):
                    raise BackendError("Cannot resolve active Plex media paths; playback was preserved")
            for part in parts:
                p = part.get("file", "")
                for kind in ("movie", "show"):
                    if p.startswith(PLEX_ROOTS[kind] + "/"):
                        _part_root(p, kind)
                        paths.append(MEDIA_ROOTS[kind] + p[len(PLEX_ROOTS[kind]):])
        return sorted(set(paths))

    def active_download_refs(self, kind: str = "any") -> set[str]:
        return set(_run_remote({"operation": "active_downloads", "kind": _kind(kind)}))

    def _verify_plex_mapping(self, item: dict):
        item = _item(item)
        for key in item.get("plex_keys", []):
            status, detail = self._plex("/library/metadata/" + key, missing=True)
            if status == 404:
                continue
            elements = detail.findall("Video" if item["kind"] == "movie" else "Directory")
            if len(elements) != 1 or elements[0].get("type") != item["kind"]:
                raise BackendError("Plex record identity changed after preview")
            descriptor = self._plex_descriptor(elements[0], item["kind"])
            if descriptor["roots"] != [item["root"]] or descriptor["standalone_file"] != item.get("standalone_file", False):
                raise BackendError("Plex media directory changed after preview")
            if item["kind"] == "show":
                _, leaves = self._plex("/library/metadata/" + key + "/allLeaves")
                if any(_part_root(part.get("file"), "show") != item["root"] for part in leaves.findall(".//Media/Part")):
                    raise BackendError("Plex show has episodes outside the selected directory")

    def preflight(self, items: list[dict]) -> dict:
        for item in items:
            self._verify_plex_mapping(item)
        return _run_remote({"operation": "preflight", "items": _remote_items(items)})

    def disable_collections(self, ids: list[int]) -> list[dict]:
        if not isinstance(ids, list) or len(ids) > 100:
            raise BackendError("Invalid collection selection")
        return _run_remote({"operation": "disable_collections", "ids": sorted({_integer(x, "collection identifier") for x in ids})})

    def delete_arr(self, item: dict) -> dict:
        item = _item(item)
        if not item.get("arr_service"):
            return {"ref": item["ref"], "arr_removed": True, "import_exclusion": "not_managed"}
        self._verify_plex_mapping(item)
        if any(path == item["root"] or path.startswith(item["root"] + "/") for path in self.active_paths()):
            raise BackendError("Selected media is playing; playback was preserved")
        return _run_remote({"operation": "delete", "items": _remote_items([item])})

    def delete_plex(self, item: dict) -> dict:
        item = _item(item)
        self._verify_plex_mapping(item)
        if any(path == item["root"] or path.startswith(item["root"] + "/") for path in self.active_paths()):
            raise BackendError("Selected media is playing; playback was preserved")
        # engine.py has already removed the exact planned files. Refuse to let
        # Plex become a second filesystem-deletion route for new arrivals.
        if os.path.lexists(item["root"]):
            if item.get("standalone_file"):
                raise BackendError("The selected movie file remains or appeared before Plex removal; inspect status")
            if os.path.islink(item["root"]) or not os.path.isdir(item["root"]):
                raise BackendError("Media root changed before Plex removal")
            def onerror(error):
                raise BackendError("Cannot confirm media files are absent before Plex removal")
            for directory, dirs, files in os.walk(item["root"], followlinks=False, onerror=onerror):
                if files or any(os.path.islink(os.path.join(directory, name)) for name in dirs):
                    raise BackendError("Media files remain or appeared before Plex removal; inspect status")
        results = []
        for key in item.get("plex_keys", []):
            status, _ = self._plex("/library/metadata/" + key, missing=True)
            if status != 404:
                status, _ = self._plex("/library/metadata/" + key, method="DELETE", missing=True)
            verified, _ = self._plex("/library/metadata/" + key, missing=True)
            if verified != 404:
                raise BackendError("Plex deletion was dispatched but its record is still present", uncertain=True)
            results.append({"rating_key": key, "http_status": status, "removed": True})
        return {"ref": item["ref"], "plex_removed": True, "records": results}

    def verify(self, items: list[dict], *, check_catalog: bool = True) -> dict:
        if type(check_catalog) is not bool:
            raise BackendError("check_catalog must be a boolean")
        managed = _remote_items(items)
        arr_results = {x["ref"]: x for x in _run_remote({"operation": "verify", "items": managed})} if managed else {}
        # Native integrations can replace record IDs. A fresh catalogue catches
        # a recreated ARR/Plex entry under a new identifier at the same root.
        # One-step cleanup can instead read back exact selected IDs without
        # rebuilding the entire Plex catalogue after each operation.
        kinds = {x["kind"] for x in items}
        current_catalog = self.catalog(next(iter(kinds)) if len(kinds) == 1 else "any") if check_catalog else []
        results = []
        for item in items:
            item = _item(item)
            statuses = [self._plex("/library/metadata/" + key, missing=True)[0] for key in item.get("plex_keys", [])]
            arr = arr_results.get(item["ref"], {})
            managed_item = bool(item.get("arr_service"))
            recreated = [x for x in current_catalog if x["kind"] == item["kind"] and x["root"] == item["root"]]
            results.append({"ref": item["ref"], "managed": managed_item, "arr_removed": arr.get("arr_removed", not managed_item) and not any(x.get("arr_service") for x in recreated),
                            "plex_removed": all(status == 404 for status in statuses) and not any(x.get("plex_keys") for x in recreated),
                            "import_exclusion_verified": arr.get("import_exclusion_verified", not managed_item),
                            "import_exclusion": "verified" if managed_item and arr.get("import_exclusion_verified") else "not_managed" if not managed_item else "missing"})
        return {"arr_removed": all(x["arr_removed"] for x in results), "plex_removed": all(x["plex_removed"] for x in results),
                "exclusions_verified": all(x["import_exclusion_verified"] for x in results), "items": results}
