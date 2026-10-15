import copy
import unittest
from full_dashboard import build,add_link


class FullDashboardTests(unittest.TestCase):
    def binding(self):
        return {'player':{'entity_id':'media_player.pi_fixture'},'tv':{'entity_id':'media_player.tv_fixture'},
                'screen_sensor':'sensor.bedroom_pi_screen','sources':{k:{'entity_id':eid} for k,eid in [('family','calendar.family_fixture'),('home','calendar.home_fixture'),('reminders','todo.fixture'),('bedside','light.bedside_fixture')]},
                'cameras':{'front':{'entity_id':'camera.front_fixture','name':'Fixture front'}},'tv_sources':['Plex','Netflix','PC']}

    def test_native_dashboard_covers_manual_actions_and_keeps_unavailable_features_honest(self):
        dashboard=build(self.binding());views=dashboard['views']
        self.assertEqual([v['path'] for v in views],['player','screen','household','lg-remote','capabilities'])
        text=str(dashboard)
        for item in ['camera_popup','camera_large','clear','clock','dark','butler','media','briefing','household','connections','full-motion','still planned']:
            self.assertIn(item,text)
        self.assertNotIn('shell_command',text);self.assertNotIn('custom:',text);self.assertNotIn('http://',text)

    def test_existing_media_views_get_only_one_link_and_preserve_every_existing_card(self):
        original={'views':[{'path':'bedroom-pi','sections':[{'type':'grid','cards':[{'type':'media-control','entity':'media_player.pi_fixture'}]}]},{'path':'other','sections':[]}]}
        before=copy.deepcopy(original);result=add_link(original)
        self.assertEqual(original,before);self.assertEqual(result['views'][1],before['views'][1])
        self.assertEqual(result['views'][0]['sections'][:-1],before['views'][0]['sections'])
        with self.assertRaises(ValueError):add_link(result)

if __name__=='__main__':unittest.main()
