import copy
import unittest
from build import build_sections,hdmi1_button
from repair_hdmi1 import repair_dashboard


class HdmiOneTests(unittest.TestCase):
    def test_shortcut_targets_port_when_tv_source_is_renamed_pc(self):
        primary,_=build_sections('media_player.fixture',['Plex','PC','Polk React'])
        cards=primary['cards'][-1]['card']['cards']
        button=next(c for c in cards if c['name']=='HDMI 1 · Pi')
        self.assertEqual(button['tap_action']['data']['payload'],{'inputId':'HDMI_1'})
        self.assertEqual(button['tap_action']['perform_action'],'webostv.command')
        self.assertNotIn('HDMI_2',str(primary))

    def test_repairs_only_selected_tv_and_preserves_other_controls(self):
        original={'cards':[{'type':'tile','entity':'media_player.fixture','name':'TV'},
                           {'type':'tile','entity':'media_player.other'},
                           {'type':'tile','entity':'media_player.fixture','name':'Volume',
                            'features':[{'type':'media-player-volume-slider'}]},
                           {'type':'button','name':'HDMI 1','tap_action':{'perform_action':'media_player.select_source',
                            'data':{'entity_id':'media_player.fixture','source':'HDMI 1'}}}]}
        before=copy.deepcopy(original)
        updated,counts=repair_dashboard(original,'media_player.fixture')
        self.assertEqual(original,before)
        self.assertEqual(counts,{'replaced_shortcuts':1,'added_input_controls':1})
        self.assertEqual(updated['cards'][2:4],before['cards'][1:3])
        self.assertEqual(updated['cards'][-1],hdmi1_button('media_player.fixture'))
        again,counts=repair_dashboard(updated,'media_player.fixture')
        self.assertEqual(again,updated)
        self.assertEqual(counts,{'replaced_shortcuts':0,'added_input_controls':0})


if __name__=='__main__':unittest.main()
