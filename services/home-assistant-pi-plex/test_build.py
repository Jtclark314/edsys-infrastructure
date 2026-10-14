import copy
import importlib.util
from pathlib import Path
import unittest
from build import extend,build_view


def walk(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from walk(v)
    elif isinstance(value,list):
        for v in value:yield from walk(v)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.original={'views':[{'path':'media','sections':[{'type':'grid','cards':[{'type':'markdown','content':'Original'}]}]},
                                {'path':'master-bedroom','sections':[]},
                                {'path':'other','cards':[{'type':'entities','entities':['light.original']}]}]}

    def test_preserves_every_existing_card_and_refuses_duplicate(self):
        before=copy.deepcopy(self.original)
        result=extend(self.original,'media_player.test_pi','media_player.test_tv')
        self.assertEqual(self.original,before)
        self.assertEqual(result['views'][2],before['views'][2])
        for i in (0,1):self.assertEqual(result['views'][i]['sections'][:-1],before['views'][i]['sections'])
        with self.assertRaises(ValueError):extend(result,'media_player.test_pi','media_player.test_tv')

    def test_transport_and_navigation_cannot_target_lg_or_arbitrary_rpc(self):
        view=build_view('media_player.test_pi','media_player.test_tv')
        methods={'Input.Home','Input.Up','Input.Down','Input.Left','Input.Right','Input.Select','Input.Back','Input.Info','Input.ContextMenu','Input.ExecuteAction','GUI.ActivateWindow','Addons.ExecuteAddon'}
        for obj in walk(view):
            if obj.get('action')!='perform-action':continue
            service=obj['perform_action'];target=obj['target']['entity_id'];data=obj['data']
            if service=='kodi.call_method':
                self.assertEqual(target,'media_player.test_pi');self.assertIn(data['method'],methods)
                if data['method']=='Addons.ExecuteAddon':self.assertEqual(data['addonid'],'script.plexmod')
            elif service in ['media_player.media_play','media_player.media_pause','media_player.media_stop']:
                self.assertEqual(target,'media_player.test_pi')
            else:
                self.assertEqual(target,'media_player.test_tv')
                self.assertIn(service,['webostv.command','media_player.volume_up','media_player.volume_down'])
                if service=='webostv.command':self.assertEqual(data,{'command':'tv/switchInput','payload':{'inputId':'HDMI_1'}})
        with self.assertRaises(ValueError):build_view('all','media_player.test_tv')

    def test_firewall_addition_is_one_exact_source_and_preserves_rest(self):
        p=Path(__file__).resolve().parents[1]/'pi5-bedroom-display/pi/enable-ha-control.py'
        spec=importlib.util.spec_from_file_location('control_policy',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        anchor='    type filter hook input priority filter; policy drop;\n'
        before='prefix\n'+anchor+'    existing rule\nsuffix\n'
        after=module.firewall_policy(before,'192.0.2.75')
        added=[l for l in after.splitlines(keepends=True) if 'EdSys HA Kodi HTTP' in l]
        self.assertEqual(len(added),1);self.assertIn('ip saddr 192.0.2.75 tcp dport 8080',added[0])
        self.assertEqual(after.replace(added[0],''),before)
        for address in ['0.0.0.0','127.0.0.1','224.0.0.1','::1','192.0.2.0/24']:
            with self.subTest(address=address),self.assertRaises(ValueError):module.firewall_policy(before,address)
        with self.assertRaises(ValueError):module.firewall_policy(after,'192.0.2.75')


if __name__=='__main__':unittest.main()
