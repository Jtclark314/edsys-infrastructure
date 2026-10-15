"""Remote vocabulary, spoken parameters and dashboard recipe coverage."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'remote_policy_fixture'
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'custom_components/edsys_voice_knowledge')]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.remote_policy', Path(package.__path__[0]) / 'remote_policy.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class RemotePolicyTests(unittest.TestCase):
    def test_local_commands_and_cloud_paraphrase_boundary(self):
        for phrase, role, action in [('Pause Plex','active','pause'),('Resume the Pi','pi','play'),
                ('Press right on the LG remote','tv','right'),('Show Pi','pi','hdmi1'),
                ('Turn the TV off','active','power_off'),('Set TV volume to twenty five percent','active','set_volume')]:
            with self.subTest(phrase=phrase):
                command=p.local_command(phrase)
                self.assertEqual(command[:2],(role,action));p.validate(phrase,*command)
        phrase='Could you lower the sound just a little on the TV?'
        self.assertTrue(p.media_text(phrase));self.assertIsNone(p.local_command(phrase))
        p.validate(phrase,'active','volume_down')
        for phrase in ['Resume the Pi Plex player','Play Plex','Play the current show on the TV']:
            p.validate(phrase,'active','play')
        for phrase in ['Stop the vacuum','Turn off kitchen lights','Show the front porch camera']:
            self.assertFalse(p.media_text(phrase))

    def test_reject_mismatched_negated_scheduled_wrong_room_and_compound(self):
        for text,op in [('Do not pause Plex','pause'),("Don't pause Plex",'pause'),('Pause Plex tomorrow','pause'),
                ('Pause Plex in 30 seconds','pause'),('If I leave pause Plex','pause'),('Pause Plex and mute TV','pause'),
                ('Pause secondary bedroom Plex','pause'),('Pause bedroom Plex','pause'),('Why is Plex paused','pause'),
                ('Unmute the TV','mute'),('Pause Plex','play'),('Press up five times','up'),('Play Titanic on Plex','play'),
                ('Play a new movie on Plex','play')]:
            with self.subTest(text=text),self.assertRaises(p.PilotRefusal):p.validate(text,'active',op)

    def test_numeric_values_are_tied_to_the_spoken_request(self):
        good=[('TV volume twenty five percent','set_volume',25),('Press seven on the TV','digit',7),
              ('Channel 42 on the TV','channel',42),('Jump to 15:30 on the Pi','seek',930),
              ('Skip forward two minutes on Plex','seek_relative',120),('Rewind thirty seconds on Plex','seek_relative',-30)]
        for text,op,value in good:
            with self.subTest(text=text):p.validate(text,'active',op,value)
        for text,op,value in [('TV volume 25 percent','set_volume',80),('TV volume 200','set_volume',200),
                ('Press seven on TV','digit',8),('Skip forward 30 seconds','seek_relative',-30),
                ('Rewind 30 seconds','seek_relative',30),('Jump to 15:30','seek',100),('TV volume 25','set_volume',True)]:
            with self.subTest(text=text),self.assertRaises(p.PilotRefusal):p.validate(text,'active',op,value)
        p.validate('Open Netflix on LG','tv','select_source',source='Netflix')
        with self.assertRaises(p.PilotRefusal):p.validate('Open Netflix on LG','tv','select_source',source='YouTube')

    def test_every_dashboard_button_has_a_fixed_voice_recipe(self):
        def load(name,path):
            spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
        lg=load('lg_remote_fixture',ROOT.parent/'home-assistant-lg-remote/build.py')
        pi=load('pi_remote_fixture',ROOT.parent/'home-assistant-pi-plex/build.py')
        def actions(value):
            if isinstance(value,dict):
                if value.get('action')=='perform-action':yield value['perform_action'],value.get('data',{})
                for child in value.values():yield from actions(child)
            elif isinstance(value,list):
                for child in value:yield from actions(child)
        def signature(service,data):
            data={k:v for k,v in data.items() if k!='entity_id'}
            return service,repr(sorted(data.items()))
        coverage=set()
        for target in ['tv','pi']:
            for op in p.OPERATIONS:
                try:
                    d,s,data=p.recipe(target,op,5 if op=='digit' else 25,'Netflix')
                    coverage.add(signature(d+'.'+s,data))
                except p.PilotRefusal:pass
        for number in range(10):coverage.add(signature('webostv.button',{'button':str(number)}))
        # Native volume services produce the same fixed LG actions as the keys.
        coverage.update(signature('webostv.button',{'button':key}) for key in ['VOLUMEUP','VOLUMEDOWN','MUTE'])
        for service,data in actions([lg.build_sections('media_player.tv',['Netflix']),pi.build_view('media_player.pi','media_player.tv')]):
            with self.subTest(service=service,data=data):self.assertIn(signature(service,data),coverage)


if __name__=='__main__':unittest.main()
