"""Exercise real clock/frame and authority boundaries, with no Kodi dependency."""
from pathlib import Path
import sys
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from character import Character,FRAMES,FPS,animation_files,frame_path,pose_for_view


class CharacterTests(unittest.TestCase):
    def test_speaking_requires_responding_and_actual_audio(self):
        self.assertEqual(pose_for_view('responding',False),'idle')
        self.assertEqual(pose_for_view('responding',True),'speaking')
        self.assertEqual(pose_for_view('failed',True),'error')
        self.assertEqual(pose_for_view('unavailable',True),'error')
        self.assertEqual(pose_for_view('processing',True),'thinking')

    def test_fixed_paths_no_arbitrary_assets_and_loop_boundary(self):
        self.assertEqual(len(animation_files()),288)
        for pose in ['../secrets','https://example.com','',None]:
            with self.assertRaises(ValueError):frame_path('/fixed',pose,1)
        for t in [True,-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):frame_path('/fixed','idle',t)
        self.assertEqual(frame_path('/fixed','idle',FRAMES/FPS).name,'idle-000.png')

    def test_same_frame_does_not_reload_and_greeting_finishes_in_idle(self):
        image=Mock()
        with patch('character.time.monotonic',return_value=10):
            c=Character(image,Path('/fixed'),'greeting',greeting=True)
        self.assertTrue(c.tick(10));self.assertFalse(c.tick(10.01))
        self.assertTrue(c.tick(10.1));self.assertEqual(image.setImage.call_count,2)
        self.assertTrue(c.tick(14));self.assertEqual(c.status['character_pose'],'idle')
        self.assertTrue(c.tick(14.1));self.assertEqual(c.status['character_frame'],1)

    def test_manual_butler_uses_greeting_then_idle_and_cards_never_talk(self):
        class Window:
            def getWidth(self):return 1920
            def getHeight(self):return 1080
            def addControl(self,*args):pass
            def addControls(self,*args):pass
        class Control:
            def __init__(self,*args,**kwargs):pass
            def setText(self,*args):pass
            def setImage(self,*args):pass
        gui=SimpleNamespace(WindowDialog=Window,ControlImage=Control,ControlLabel=Control,ControlTextBox=Control)
        path=Path(__file__).resolve().parents[1]/'kodi/manual_display.py'
        with patch.dict(sys.modules,{'xbmc':SimpleNamespace(Monitor=object),'xbmcgui':gui}):
            spec=importlib.util.spec_from_file_location('manual_character_fixture',path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            message={'kind':'butler','heading':'Phil','body':'','rows':[]}
            with patch('character.time.monotonic',return_value=10):w=module.ScreenWindow(message)
            self.assertEqual(w.character.pose,'greeting');w.character.tick(14.1)
            self.assertEqual(w.character.pose,'idle')
            message['kind']='media';w=module.ScreenWindow(message)
            self.assertEqual(w.character.pose,'idle');self.assertFalse(w.character.greeting)


if __name__=='__main__':unittest.main()
