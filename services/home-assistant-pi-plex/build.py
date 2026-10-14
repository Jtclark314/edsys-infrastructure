"""Native Pi Plex view; explicit endpoint controls and preserved existing views."""
import copy
import re

PATH='bedroom-pi'
TITLE='Bedroom Pi Plex'
URL='/edsys-media/'+PATH


def entity(value):
    if not re.fullmatch(r'media_player\.[a-z0-9_]+',value):raise ValueError('Explicit media player required')
    return value


def button(name,icon,service,target,**data):
    return {'type':'button','name':name,'icon':'mdi:'+icon,'show_state':False,
            'icon_height':'28px','tap_action':{'action':'perform-action','perform_action':service,
                'target':{'entity_id':target},'data':data},
            'hold_action':{'action':'none'},'double_tap_action':{'action':'none'}}


def build_view(player,tv):
    entity(player);entity(tv)
    def action(name,icon,method,**data):return button(name,icon,'kodi.call_method',player,method=method,**data)
    def grid(cards,columns=3):return {'type':'grid','columns':columns,'square':False,'cards':cards}
    def heading(name,icon):return {'type':'heading','heading':name,'icon':'mdi:'+icon,'heading_style':'title'}
    available=[{'condition':'state','entity':player,'state_not':['unavailable','unknown']}]
    nav=grid([action('Player home','home-outline','Input.Home'),action('Up','chevron-up','Input.Up'),action('Info','information-outline','Input.Info'),
              action('Left','chevron-left','Input.Left'),action('OK','circle-outline','Input.Select'),action('Right','chevron-right','Input.Right'),
              action('Back','arrow-u-left-top','Input.Back'),action('Down','chevron-down','Input.Down'),action('Options','dots-horizontal','Input.ContextMenu')])
    controls=[heading('Browse on the TV','remote'),
              {'type':'markdown','content':'Open Plex, then use the arrows and OK to choose something on the TV.'},
              grid([action('Open Plex','plex','Addons.ExecuteAddon',addonid='script.plexmod'),
                    action('Now playing','television-play','GUI.ActivateWindow',window='fullscreenvideo')],2),nav]
    return {'title':TITLE,'path':PATH,'icon':'mdi:raspberry-pi','type':'sections','max_columns':3,
            'sections':[
                {'type':'grid','cards':[heading('Now playing on the Pi','plex'),
                 {'type':'media-control','entity':player,'name':'Bedroom Pi Plex'},
                 {'type':'conditional','conditions':available,'card':grid([
                    button('Play','play','media_player.media_play',player),
                    button('Pause','pause','media_player.media_pause',player),
                    button('Stop','stop','media_player.media_stop',player)])},
                 {'type':'conditional','conditions':available,'card':grid([
                    action('Skip back','replay','Input.ExecuteAction',action='stepback'),
                    action('Skip forward','fast-forward','Input.ExecuteAction',action='stepforward')],2)}]},
                {'type':'grid','cards':[{'type':'conditional','conditions':available,
                    'card':{'type':'vertical-stack','cards':controls}}]},
                {'type':'grid','cards':[heading('TV and soundbar','television-speaker'),
                    button('Show Pi · HDMI 1','video-input-hdmi','webostv.command',tv,
                           command='tv/switchInput',payload={'inputId':'HDMI_1'}),
                    {'type':'tile','entity':tv,'name':'TV / soundbar volume',
                     'features':[{'type':'media-player-volume-slider'}]},
                    grid([button('Volume down','volume-minus','media_player.volume_down',tv),
                          button('Volume up','volume-plus','media_player.volume_up',tv)],2),
                    {'type':'button','name':'LG TV remote','icon':'mdi:remote-tv',
                     'tap_action':{'action':'navigate','navigation_path':'/edsys-media/master-bedroom'}}]}]}


def link_button():
    return {'type':'button','name':TITLE,'icon':'mdi:raspberry-pi','show_state':False,
            'tap_action':{'action':'navigate','navigation_path':URL},
            'hold_action':{'action':'none'},'double_tap_action':{'action':'none'}}


def extend(original,player,tv):
    result=copy.deepcopy(original)
    if any(v.get('path')==PATH for v in result['views']):raise ValueError('Pi view already exists; inspect rather than duplicate')
    for path in ['media','master-bedroom']:
        views=[v for v in result['views'] if v.get('path')==path]
        if len(views)!=1:raise ValueError('Expected exactly one existing '+path+' view')
        views[0]['sections'].append({'type':'grid','cards':[link_button()]})
    result['views'].append(build_view(player,tv))
    return result
