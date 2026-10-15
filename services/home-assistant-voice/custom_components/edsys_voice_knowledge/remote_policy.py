"""Reviewed bedroom remote vocabulary and fixed service recipes; no device I/O."""
from __future__ import annotations

import math
import re
from .pilot_policy import PilotRefusal, normalize

TV_BUTTONS = {'up':'UP','down':'DOWN','left':'LEFT','right':'RIGHT','select':'ENTER',
              'back':'BACK','home':'HOME','info':'INFO','options':'ASTERISK',
              'exit':'EXIT','settings':'MENU','guide':'GUIDE','channel_up':'CHANNELUP',
              'channel_down':'CHANNELDOWN','captions':'CC','red':'RED','green':'GREEN',
              'yellow':'YELLOW','blue':'BLUE','dash':'DASH','enter':'ENTER'}
PI_METHODS = {'up':'Input.Up','down':'Input.Down','left':'Input.Left','right':'Input.Right',
              'select':'Input.Select','back':'Input.Back','home':'Input.Home',
              'info':'Input.Info','options':'Input.ContextMenu'}
TV_ONLY = {'power_on','power_off','volume_up','volume_down','set_volume','mute','unmute',
           'select_source','hdmi1','hdmi2','digit','channel','dash','enter','guide',
           'channel_up','channel_down','red','green','yellow','blue','settings','exit'}
PLAYBACK = {'play','pause','stop','previous','next','seek','seek_relative','skip_back','skip_forward'}
OPERATIONS = sorted(set(TV_BUTTONS)|set(PI_METHODS)|TV_ONLY|PLAYBACK|
                    {'open_plex','now_playing','rewind','fast_forward','subtitles_on','subtitles_off'})
WORDS = {
 'power_on':r'\b(on|wake)\b', 'power_off':r'\b(off|shut.*down|power.*down)\b',
 'volume_up':r'\b(louder|increase|raise|up)\b','volume_down':r'\b(quieter|softer|lower|decrease|reduce|down|too loud|less loud)\b',
 'set_volume':r'\b(volume|sound|percent)\b','mute':r'\b(mute|silence|silent|quiet)\b',
 'unmute':r'\b(unmute|restore.*sound|sound.*back)\b',
 'play':r'\b(play|resume|continue|unpause)\b','pause':r'\b(pause|hold|freeze)\b','stop':r'\bstop\b',
 'previous':r'\b(previous|last)\b','next':r'\bnext\b','seek':r'\b(seek|go to|jump to|start at)\b',
 'seek_relative':r'\b(skip|jump|rewind|forward|back)\b','skip_back':r'\b(back|rewind)\b',
 'skip_forward':r'\b(forward|ahead)\b','open_plex':r'\b(open|launch|bring up|show|start)\b.*\bplex\b',
 'now_playing':r'\b(now playing|current.*video|back.*video|show.*video)\b',
 'hdmi1':r'\b(hdmi 1|hdmi one|show.*pi|switch.*pi|pi.*input)\b',
 'hdmi2':r'\b(hdmi 2|hdmi two)\b', 'select_source':r'\b(open|launch|switch|select|change|watch|show)\b',
 'select':r'\b(ok|okay|select|choose|confirm)\b', 'home':r'\bhome\b','back':r'\bback\b',
 'up':r'\bup\b','down':r'\bdown\b','left':r'\bleft\b','right':r'\bright\b',
 'info':r'\b(info|information|details)\b','options':r'\b(options|context menu|asterisk)\b',
 'exit':r'\b(exit|close)\b','settings':r'\b(settings|menu)\b','guide':r'\bguide\b',
 'channel_up':r'\b(channel.*up|next channel)\b','channel_down':r'\b(channel.*down|previous channel)\b',
 'captions':r'\b(captions|subtitles|closed caption)\b','subtitles_on':r'\b(captions|subtitles)\b.*\bon\b|\bon\b.*\b(captions|subtitles)\b',
 'subtitles_off':r'\b(captions|subtitles)\b.*\boff\b|\boff\b.*\b(captions|subtitles)\b',
 'rewind':r'\brewind\b','fast_forward':r'\b(fast forward|fastforward)\b',
 'digit':r'\b(press|number|digit|enter|type)\b','channel':r'\bchannel\b',
 'dash':r'\b(dash|hyphen)\b','enter':r'\benter\b',
 **{c:rf'\b{c}\b' for c in ['red','green','yellow','blue']},
}
SMALL = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(),range(20)))
TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(),range(20,100,10)))


def numbers(text):
    """Small spoken numbers used for explicit volume/key/seek parameters."""
    q=normalize(text);out=[float(x) for x in re.findall(r'\b\d+(?:\.\d+)?\b',text)]
    tokens=q.split();i=0
    while i<len(tokens):
        word=tokens[i]
        if word in SMALL or word in TENS or word=='hundred':
            n=SMALL.get(word,TENS.get(word,100));i+=1
            if i<len(tokens) and tokens[i]=='hundred':n*=100;i+=1
            if i<len(tokens) and tokens[i] in TENS:n+=TENS[tokens[i]];i+=1
            if i<len(tokens) and tokens[i] in SMALL and tokens[i]!='zero':n+=SMALL[tokens[i]];i+=1
            out.append(float(n))
        else:i+=1
    return out


def check_text(text):
    if not isinstance(text,str) or not 1<=len(text)<=512:raise PilotRefusal('Please repeat one short media command.')
    q=normalize(text)
    if re.search(r'\b(do not|don t|never|unless|if|when|later|tomorrow|schedule|every|and|then|except)\b',q) or re.search(r'\bin \d+ (seconds?|minutes?|hours?)\b',q):
        raise PilotRefusal('Please give one immediate remote command at a time.')
    if re.search(r'\b(secondary|guest|living room|movie room|office|kitchen|garage|patio)\b',q):
        raise PilotRefusal('These remote voice controls cover Master Bedroom. Please name that screen or use its remote.')
    if 'bedroom' in q and not re.search(r'\b(master|main|primary) bedroom\b',q):
        raise PilotRefusal('Which bedroom: Master or Secondary?')
    if re.match(r'^(what|why|how|does|is|are|would it|tell me|explain|describe)\b',q):
        raise PilotRefusal('That is a question, not a command. I can describe the current player without changing it.')
    return q


def validate(text,target,operation,value=None,source=None):
    q=check_text(text)
    if target not in ('active','tv','pi') or operation not in OPERATIONS:raise PilotRefusal('That remote action is not available.')
    if not re.search(WORDS[operation],q):raise PilotRefusal('Please state the remote action you want; I will not substitute a different button.')
    if operation in PI_METHODS and re.search(r'\b(volume|channel)\b',q):raise PilotRefusal('Please separate navigation from volume or channel control.')
    if operation in ('play','pause','stop'):
        other={'play':r'\b(pause|stop)\b','pause':r'\b(resume|unpause|stop)\b','stop':r'\b(pause|resume|unpause)\b'}[operation]
        if re.search(other,q):raise PilotRefusal('The command names conflicting playback actions.')
    if operation=='play':
        # Playback resumes the current item. A title is not a transport command.
        remaining=re.sub(r'\b(play|resume|continue|unpause|the|a|my|this|current|paused|playing|video|episode|movie|film|show|media|playback|player|content|program|programme|on|in|at|of|master|main|primary|bedroom|tv|television|screen|lg|plex|kodi|pi|raspberry|please|could|can|would|you|will|me|for|now|it|again)\b',' ',q)
        if remaining.strip():raise PilotRefusal('Open Plex and select that title with the remote; this command resumes the current item.')
    if target=='pi' and operation in TV_ONLY and operation not in {'volume_up','volume_down','set_volume','mute','unmute','hdmi1'}:
        raise PilotRefusal('That button belongs to the LG TV remote. Please name the LG.')
    if operation in {'set_volume','seek','seek_relative','digit','channel'}:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise PilotRefusal('Please give a numeric setting.')
        ns=numbers(text)
        if operation in {'set_volume','digit','channel'}:
            limit={'set_volume':100,'digit':9,'channel':99999}[operation]
            if not 0<=value<=limit or not any(abs(n-value)<.001 for n in ns):raise PilotRefusal('The number must match the spoken request and the remote range.')
            if operation in {'digit','channel'} and int(value)!=value:raise PilotRefusal('Please use a whole number.')
        else:
            candidates=set(ns)
            for n in ns:
                if re.search(r'\bminutes?\b',q):candidates.add(n*60)
                if re.search(r'\bhours?\b',q):candidates.add(n*3600)
            for m in re.finditer(r'\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b',text):
                parts=[int(x) for x in m.groups() if x is not None];candidates.add(sum(n*60**i for i,n in enumerate(reversed(parts))))
            if not 0<=abs(value)<=86400 or not any(abs(n-abs(value))<.001 for n in candidates):raise PilotRefusal('Please give a clear seek position or offset.')
            if operation=='seek' and value<0:raise PilotRefusal('A playback position cannot be negative.')
            if operation=='seek_relative' and ((value<0 and not re.search(r'\b(back|backward|backwards|rewind)\b',q)) or (value>0 and not re.search(r'\b(forward|ahead|forwards)\b',q))):raise PilotRefusal('Seek direction must match the command.')
    elif value is not None:raise PilotRefusal('That remote button does not accept a number.')
    elif operation not in {'hdmi1','hdmi2'} and numbers(text):raise PilotRefusal('Please use one button press, an exact volume, or a timed seek.')
    if operation=='select_source':
        if not isinstance(source,str) or not 1<=len(source)<=80:raise PilotRefusal('Please name an available TV app or input.')
        if normalize(source) not in q:raise PilotRefusal('The app/input must match the spoken request.')
    elif source is not None:raise PilotRefusal('That button does not accept an app name.')
    return q


def local_command(text):
    """Recognize only unambiguous fast paths; other phrasing goes to cloud tools."""
    q=normalize(text)
    if re.match(r'^(what|why|how|does|is|are|tell me|explain|describe)\b',q):return None
    target='pi' if re.search(r'\b(pi|raspberry|kodi)\b',q) else ('tv' if re.search(r'\b(lg|tv remote|television remote)\b',q) else 'active')
    ns=numbers(text)
    if 'volume' in q and len(ns)==1:return target,'set_volume',ns[0],None
    patterns=[('volume_up',r'\b(volume up|turn.*volume.*up|louder|sound.*up)\b'),('volume_down',r'\b(volume down|turn.*volume.*down|quieter|softer|sound.*down)\b'),
              ('unmute',r'\b(unmute|restore.*sound)\b'),('mute',r'\b(mute|silence)\b'),
              ('pause',r'\bpause\b'),('play',r'\b(resume|continue|unpause)\b'),('stop',r'\bstop\b'),
              ('hdmi1',r'\bhdmi (1|one)\b|\b(show|switch to) (?:the )?pi\b'),('hdmi2',r'\bhdmi (2|two)\b'),
              ('power_off',r'\b(turn|switch|power) off\b|\bturn.*tv off\b'),('power_on',r'\b(turn|switch|power) on\b|\bwake\b'),
              ('open_plex',r'\b(open|launch) plex\b'),('now_playing',r'\b(show|return to) now playing\b')]
    for operation,pattern in patterns:
        if re.search(pattern,q):
            if operation in ('power_on','power_off') and not re.search(r'\b(tv|television|screen|lg)\b',q):continue
            return target,operation,None,None
    # Explicit key presses without paraphrasing stay local. Bare ambiguous words
    # such as "right" are left to conversation rather than triggering a key.
    for operation in TV_BUTTONS:
        label={'select':'(?:ok|okay|select)','options':'(?:options|asterisk)'}.get(operation,operation.replace('_',' '))
        if re.search(r'\b(press|tap|go|move) (?:the )?'+label+r'\b',q):return target,operation,None,None
    return None


def media_text(text):
    q=normalize(text)
    if re.search(r'\b(light|lights|lamp|vacuum|fan|door|lock|blinds|camera)\b',q) and not re.search(r'\b(tv|television|plex|kodi|pi|remote|hdmi)\b',q):return False
    return bool(re.search(r'\b(tv|television|plex|kodi|pi|remote|hdmi|soundbar|volume|captions|subtitles)\b',q) or local_command(text))


def recipe(target,operation,value=None,source=None):
    """Return one fixed native action; runtime validates target/current state."""
    if operation in {'play','pause','stop','previous','next'}:
        service={'play':'media_play','pause':'media_pause','stop':'media_stop','previous':'media_previous_track','next':'media_next_track'}[operation]
        return 'media_player',service,{}
    if operation=='seek':return 'media_player','media_seek',{'seek_position':value}
    if operation in {'power_on','power_off','volume_up','volume_down','set_volume','mute','unmute'}:
        service={'power_on':'turn_on','power_off':'turn_off','set_volume':'volume_set','mute':'volume_mute','unmute':'volume_mute'}.get(operation,operation)
        data={'volume_level':value/100} if operation=='set_volume' else ({'is_volume_muted':operation=='mute'} if operation in ('mute','unmute') else {})
        return 'media_player',service,data
    if operation=='select_source':return 'media_player','select_source',{'source':source}
    if operation in ('hdmi1','hdmi2'):return 'webostv','command',{'command':'tv/switchInput','payload':{'inputId':'HDMI_'+operation[-1]}}
    if target=='pi':
        if operation in PI_METHODS:return 'kodi','call_method',{'method':PI_METHODS[operation]}
        if operation=='open_plex':return 'kodi','call_method',{'method':'Addons.ExecuteAddon','addonid':'script.plexmod'}
        if operation=='now_playing':return 'kodi','call_method',{'method':'GUI.ActivateWindow','window':'fullscreenvideo'}
        if operation in {'skip_back','skip_forward','rewind','fast_forward'}:
            return 'kodi','call_method',{'method':'Input.ExecuteAction','action':{'skip_back':'stepback','skip_forward':'stepforward','rewind':'rewind','fast_forward':'fastforward'}[operation]}
        if operation in {'subtitles_on','subtitles_off'}:return 'kodi','call_method',{'method':'Player.SetSubtitle','playerid':1,'subtitle':'on' if operation.endswith('on') else 'off'}
        if operation=='captions':return 'kodi','call_method',{'method':'Input.ExecuteAction','action':'showsubtitles'}
    else:
        if operation in TV_BUTTONS:return 'webostv','button',{'button':TV_BUTTONS[operation]}
        if operation=='digit':return 'webostv','button',{'button':str(int(value))}
        if operation in {'rewind','fast_forward'}:return 'webostv','command',{'command':'media.controls/rewind' if operation=='rewind' else 'media.controls/fastForward'}
    raise PilotRefusal('That action is not supported for the selected remote.')
