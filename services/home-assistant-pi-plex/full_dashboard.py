"""Complete native Bedroom Pi dashboard; live bindings are supplied privately."""
import copy
import importlib.util
from pathlib import Path

from build import build_view,button,entity

URL='edsys-pi'


def heading(title,icon):return {'type':'heading','heading':title,'heading_style':'title','icon':'mdi:'+icon}
def grid(cards,columns=3):return {'type':'grid','columns':columns,'square':False,'cards':cards}
def section(cards):return {'type':'grid','cards':cards}
def note(text):return {'type':'markdown','content':text}
def nav(title,icon,path):return {'type':'button','name':title,'icon':'mdi:'+icon,'tap_action':{'action':'navigate','navigation_path':path}}


def screen(title,icon,action,**data):
    return {'type':'button','name':title,'icon':'mdi:'+icon,'icon_height':'30px','show_state':False,
            'tap_action':{'action':'perform-action','perform_action':'edsys_voice_knowledge.bedroom_screen','data':{'action':action,**data}},
            'hold_action':{'action':'none'},'double_tap_action':{'action':'none'}}


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
        note('Use **Show Pi · HDMI 1** before opening a TV card. A popup leaves Plex playback alone. **Dismiss popup** returns to the same picture.')]))

    screen_cards=[heading('TV screen controls','monitor-dashboard'),
        {'type':'tile','entity':sensor,'name':'Current screen'},
        note('Choose **Show Pi · HDMI 1** on Player first. Camera previews use refreshing images, not full-motion video. Cards close automatically; **Dismiss popup** or Back closes them sooner.'),
        grid([screen('Dismiss popup','close','clear'),screen('Show butler','account-tie','butler'),screen('Media details','plex','media')]),
        grid([screen('Clock · 5 min','clock-outline','clock',duration=300),screen('Dark screen · 5 min','weather-night','dark',duration=300),screen('Bedroom overview','home-heart','household',duration=60)]),
        grid([screen('Daily briefing','calendar-today','briefing',duration=90),screen('Connection status','lan-check','connections',duration=60)],2),
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
| Device status | Manual availability card |
'''),nav('TV screen controls','monitor-dashboard','/'+URL+'/screen')]),
                   section([heading('Still planned','progress-clock'),note('''Voice-synchronised animation and captions, AI visual answers, full-motion camera inset, weather, real door/temperature readings, approved photo/art sources, and automatic problem notifications need further setup or qualification.

The controls above use current sources. Missing capabilities are shown here until they have a working connection and a screen test. Camera AI analysis stays off.''')])]}
    return {'title':'Bedroom Pi','views':[player_view,screen_view,household,lg_view,plan]}


def add_link(original):
    result=copy.deepcopy(original)
    view=next(v for v in result['views'] if v.get('path')=='bedroom-pi')
    link=nav('Full Bedroom Pi dashboard','monitor-dashboard','/'+URL+'/player')
    if any('Full Bedroom Pi dashboard' in str(s) for s in view.get('sections',[])):raise ValueError('Full dashboard link already exists')
    view['sections'].append(section([link]));return result
