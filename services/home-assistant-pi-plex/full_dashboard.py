"""Complete native Bedroom Pi dashboard; live bindings are supplied privately."""
import copy
import importlib.util
from pathlib import Path

from build import build_view,button,entity
from weather_dashboard import extend as extend_weather

URL='edsys-pi'


def heading(title,icon):return {'type':'heading','heading':title,'heading_style':'title','icon':'mdi:'+icon}
def grid(cards,columns=3):return {'type':'grid','columns':columns,'square':False,'cards':cards}
def section(cards):return {'type':'grid','cards':cards}
def note(text):return {'type':'markdown','content':text}
def nav(title,icon,path):return {'type':'button','name':title,'icon':'mdi:'+icon,'tap_action':{'action':'navigate','navigation_path':path}}


def music_access():
    spec=importlib.util.spec_from_file_location('bedroom_music_access',Path(__file__).resolve().parents[1]/'music-assistant/dashboard_access.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def music_launcher():
    return music_access().shortcut()


def screen(title,icon,action,**data):
    return {'type':'button','name':title,'icon':'mdi:'+icon,'icon_height':'30px','show_state':False,
            'tap_action':{'action':'perform-action','perform_action':'edsys_voice_knowledge.bedroom_screen','data':{'action':action,**data}},
            'hold_action':{'action':'none'},'double_tap_action':{'action':'none'}}


def comfort(title,icon,action,**data):
    card=screen(title,icon,action,**data)
    card['tap_action']['perform_action']='edsys_voice_knowledge.bedroom_comfort'
    return card


def build(binding):
    player=entity(binding['player']['entity_id']);tv=entity(binding['tv']['entity_id'])
    sensor=binding['screen_sensor'];sources=binding['sources'];cameras=binding['cameras']
    player_view=build_view(player,tv)
    player_view.update(title='Player',path='player',icon='mdi:plex')
    player_view['sections'][2]['cards'][-1]=nav('Full LG remote','remote-tv','/'+URL+'/lg-remote')
    player_view['sections'][2]['cards'].insert(2,{'type':'tile','entity':tv,'name':'LG apps & inputs','features':[{'type':'media-player-source'}]})
    player_view['sections'][2]['cards'].extend([
        grid([button('Wake TV','power','media_player.turn_on',tv),button('TV off','power-standby','media_player.turn_off',tv)],2),
        grid([button('Mute','volume-off','media_player.volume_mute',tv,is_volume_muted=True),button('Unmute','volume-high','media_player.volume_mute',tv,is_volume_muted=False)],2)])
    player_view['sections'].append(section([heading('On the TV','television-shimmer'),
        {'type':'tile','entity':sensor,'name':'Pi screen'},
        grid([screen('Dismiss popup','close','clear'),screen('Show butler','account-tie','butler'),screen('Media details','information','media')]),
        nav('Cameras & screen cards','monitor-dashboard','/'+URL+'/screen'),
        nav('Night controls, sleep & artwork','weather-night','/'+URL+'/night'),
        note('Use **Show Pi · HDMI 1** before opening a TV card. A popup leaves Plex playback alone. **Dismiss popup** returns to the same picture.')]))

    screen_cards=[heading('TV screen controls','monitor-dashboard'),
        {'type':'tile','entity':sensor,'name':'Current screen'},
        note('Choose **Show Pi · HDMI 1** on Player first. Camera previews use refreshing images, not full-motion video. Cards close automatically; **Dismiss popup** or Back closes them sooner.'),
        grid([screen('Dismiss popup','close','clear'),screen('Show butler','account-tie','butler'),screen('Media details','plex','media')]),
        grid([screen('Clock · 5 min','clock-outline','clock',duration=300),screen('Dark screen · 5 min','weather-night','dark',duration=300),screen('Bedroom overview','home-heart','household',duration=60)]),
        grid([screen('Daily briefing','calendar-today','briefing',duration=90),screen('Connection status','lan-check','connections',duration=60)],2),
        nav('Artwork & night controls','image-multiple','/'+URL+'/night'),
        note('Family, Home and Reminders stay local. Voice-synchronised butler animation and AI answer cards are still planned.')]
    camera_cards=[heading('Camera popups on the TV','cctv'),note('A popup lasts 30 seconds. Large preview lasts 60 seconds. Neither starts a second video player or changes the movie’s play/pause state.')]
    for key,camera in cameras.items():
        camera_cards.extend([{'type':'tile','entity':camera['entity_id'],'name':camera['name'],'icon':'mdi:cctv','tap_action':{'action':'none'}},
            grid([screen('Popup','picture-in-picture-top-right','camera_popup',camera=key,duration=30),screen('Large preview','fullscreen','camera_large',camera=key,duration=60)],2)])
    screen_view={'title':'TV screen','path':'screen','icon':'mdi:monitor-dashboard','type':'sections','max_columns':2,
                 'sections':[section(screen_cards),section(camera_cards)]}

    household={'title':'Household','path':'household','icon':'mdi:home-heart','type':'sections','max_columns':3,
       'sections':[section([heading('Bedroom','lightbulb-night'),
                    {'type':'tile','entity':sources['bedside']['entity_id'],'name':'Bedside light','features':[{'type':'light-brightness'}]},
                    grid([screen('Bedroom overview on TV','television','household',duration=60),screen('Daily briefing on TV','calendar-today','briefing',duration=90)],2),
                    note('Door, temperature and weather sources are not configured. The overview shows the devices that are available.')]),
                   section([heading('Family & Home','calendar'),{'type':'calendar','entities':[sources[k]['entity_id'] for k in ['family','home']],'initial_view':'listWeek'}]),
                   section([heading('Reminders','format-list-checks'),{'type':'todo-list','entity':sources['reminders']['entity_id']}])]}
    spec=importlib.util.spec_from_file_location('bedroom_lg_dashboard',Path(__file__).resolve().parents[1]/'home-assistant-lg-remote/build.py')
    lg=importlib.util.module_from_spec(spec);spec.loader.exec_module(lg)
    primary,secondary=lg.build_sections(tv,binding['tv_sources'])
    lg_view={'title':'LG remote','path':'lg-remote','icon':'mdi:remote-tv','type':'sections','max_columns':3,
             'sections':[primary,secondary,section([heading('Apps & inputs','video-input-hdmi'),{'type':'media-control','entity':tv},
                       {'type':'tile','entity':tv,'name':'LG apps & inputs','features':[{'type':'media-player-source'}]},nav('Pi player','raspberry-pi','/'+URL+'/player')])]}
    night_sensor=binding.get('night_sensor','sensor.bedroom_pi_night_controls')
    sleep_sensor=binding.get('sleep_sensor','sensor.bedroom_pi_sleep_timer')
    night={'title':'Night & art','path':'night','icon':'mdi:weather-night','type':'sections','max_columns':3,
      'sections':[section([heading('Dialogue & subtitles','subtitles-outline'),
        {'type':'tile','entity':night_sensor,'name':'Pi night controls'},
        note("**Night compression:** {{ 'On' if state_attr('"+night_sensor+"', 'night_compression') else 'Off' }}\n\n**Subtitles:** {{ 'On' if state_attr('"+night_sensor+"', 'subtitles_enabled') else 'Off' }} · {{ state_attr('"+night_sensor+"', 'subtitle_track') or 'No track' }}\n\n**Text size:** {{ state_attr('"+night_sensor+"', 'subtitle_size') }} · **Timing:** {{ state_attr('"+night_sensor+"', 'subtitle_delay') or 'Unavailable' }}"),
        grid([comfort('Night compression on','volume-medium','night_on'),comfort('Original dynamics','volume-high','night_off')],2),
        comfort('Dialogue / audio options on TV','tune','audio_options'),
        note('Night compression helps quiet dialogue during multichannel-to-stereo playback. Audio options includes the centre-channel mix control. Use the Pi arrows and Back on Player.'),
        grid([comfort('Subtitles on','subtitles','subtitles_on'),comfort('Full English','alphabetical','subtitles_english'),comfort('Subtitles off','subtitles-outline','subtitles_off')]),
        grid([comfort('Normal text','format-size','subtitles_normal'),comfort('Large text','format-size','subtitles_large'),comfort('Extra large','format-size','subtitles_extra_large')]),
        grid([comfort('Earlier captions','rewind','subtitles_earlier'),comfort('Later captions','fast-forward','subtitles_later'),comfort('Next track','skip-next','subtitles_next')]),
        comfort('Subtitle options on TV','tune-variant','subtitle_options'),
        note('Text sizes affect text subtitles. Picture-based or burned-in captions cannot be resized this way.')]),
       section([heading('Sleep timer','timer-outline'),
        {'type':'tile','entity':sleep_sensor,'name':'Pause Pi in'},
        note('At expiry, pauses Pi playback, including a queued next episode. The TV stays on. Nothing resumes automatically.'),
        grid([comfort(str(m)+' min','timer-outline','sleep_start',minutes=m) for m in [15,30,45]]),
        grid([comfort(str(m)+' min','timer-outline','sleep_start',minutes=m) for m in [60,90,120]]),
        comfort('Cancel sleep timer','timer-cancel-outline','sleep_cancel'),
        note("{% set finish=state_attr('"+sleep_sensor+"', 'finishes_at') %}{% if finish %}Pauses at **{{ as_local(as_datetime(finish)).strftime('%I:%M %p') }}**.{% else %}No timer is armed.{% endif %}\n\nTimers cancel after a Home Assistant restart. Playback is left alone if the Pi is unavailable or the TV is no longer showing it.")]),
       section([heading('Quiet artwork','image-multiple'),
        grid([screen('Artwork · 30 min','image','art',duration=1800),screen('Artwork · 1 hour','image-multiple','art',duration=3600)],2),
        grid([screen('Next artwork','skip-next','art_next'),screen('Return to picture','close','clear')],2),
        note('Four original calm artworks rotate every 20 seconds. Back or Return closes the artwork. Playback and audio continue underneath; pause the movie first if desired. Personal photos can be connected later.'),
        nav('Pi playback & navigation','plex','/'+URL+'/player')])]}
    plan={'title':'Capabilities','path':'capabilities','icon':'mdi:progress-check','type':'sections','max_columns':2,
       'sections':[section([heading('Ready to use','check-circle-outline'),note('''| Capability | Screen control |
| --- | --- |
| Plex playback | Player, seek/progress and Pi navigation |
| LG TV | Full remote, power, volume, apps and physical HDMI 1 |
| Camera preview | Refreshing image popup or large preview |
| Butler | Manual appearance and dismissal |
| Daily briefing | Family, Home and Reminders on TV |
| Bedroom overview | Current bedside light and media states |
| Media companion | Current title and playback details |
| Ambient display | Five-minute clock or dark screen |
| Night controls | Downmix compression, audio options and subtitle controls |
| Sleep timer | 15–120 minute countdown, cancel and Pi pause at expiry |
| Artwork | Four rotating original presets; 30 or 60 minutes |
| Device status | Manual availability card |
'''),nav('TV screen controls','monitor-dashboard','/'+URL+'/screen')]),
                   section([heading('Still planned','progress-clock'),note('''Voice-synchronised animation and captions, AI visual answers, full-motion camera inset, weather, real door/temperature readings, personal photo sources, and automatic problem notifications need further setup or qualification.

The controls above use current sources. Missing capabilities are shown here until they have a working connection and a screen test. Camera AI analysis stays off.''')])]}
    return extend_weather(music_access().extend(add_music_view({'title':'Bedroom Pi','views':[player_view,night,screen_view,household,lg_view,plan]},player),'edsys-pi')[0])


def add_link(original):
    result=copy.deepcopy(original)
    view=next(v for v in result['views'] if v.get('path')=='bedroom-pi')
    link=nav('Full Bedroom Pi dashboard','monitor-dashboard','/'+URL+'/player')
    if any('Full Bedroom Pi dashboard' in str(s) for s in view.get('sections',[])):raise ValueError('Full dashboard link already exists')
    view['sections'].append(section([link]));return result


def music_view(player):
    """Browse in the existing servers; transport controls target only the Pi."""
    entity(player)
    return {'title':'Music & books','path':'music-books','icon':'mdi:music-box-multiple','type':'sections','max_columns':2,
      'sections':[section([heading('Choose something to play','music-box-multiple'),
        music_launcher(),
        {'type':'button','name':'Open Audiobookshelf','icon':'mdi:bookshelf',
         'tap_action':{'action':'url','url_path':'http://192.168.50.50:13378/'}},
        note('In Music Assistant, click the current device name at the **bottom right** to open the player picker and choose **Bedroom Pi Plex** as the output, then browse Music or Audiobooks. Music plays through the Pi’s HDMI output to the LG/soundbar. Starting a selection replaces the current Pi playback, including Plex.'),
        note('Audiobookshelf manages books and listening progress. Its separate web player plays on the device running that browser; use Music Assistant to send a book to the Pi. Existing account sign-in is required.'),
        nav('LG input & volume','remote-tv','/'+URL+'/lg-remote')]),
       section([heading('Pi playback','raspberry-pi'),{'type':'media-control','entity':player},
        grid([button('Resume Pi','play','media_player.media_play',player),button('Pause Pi','pause','media_player.media_pause',player),button('Stop Pi','stop','media_player.media_stop',player)]),
        nav('Plex & Pi navigation','plex','/'+URL+'/player'),
        note('Choose tracks, queue order and audiobook position in Music Assistant. These Pi buttons control whatever is currently playing on the TV.'),
        note('Music and audiobook sound checks are pending. Phil’s speech pause/resume currently supports video only. Music and audiobook interruption, chapter seeking and resume still need setup or listening tests.')])]}


def add_music_view(original,player):
    """Append only the new view and one Player link, preserving live dashboard edits."""
    result=copy.deepcopy(original)
    if any(v.get('path')=='music-books' for v in result['views']):raise ValueError('Music view already exists; inspect instead of overwriting')
    current=next(v for v in result['views'] if v.get('path')=='player')
    current['sections'][0]['cards'].append(nav('Music & audiobooks','music-box-multiple','/'+URL+'/music-books'))
    result['views'].insert(1,music_view(player))
    return result
