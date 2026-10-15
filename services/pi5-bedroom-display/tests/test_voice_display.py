"""Actual display state machine with GUI controls replaced; no device I/O."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from manual_protocol import validate


class VoiceTests(unittest.TestCase):
    def message(self,state='listening',seq=0,key=None):
        return dict(version=1,request_id=key or str(uuid4()),sequence=seq,issued_at=1000,expires_at=1007,
                    command='voice',state=state,audio_playing=state=='responding')

    def test_protocol_rejects_long_lifetime_audio_without_response_and_private_data(self):
        for change in [{'expires_at':1009},{'audio_playing':True},{'state':'celebrate'},{'text':'private'},{'url':'http://x.invalid'},{'audio_playing':1}]:
            with self.subTest(change=change),self.assertRaises(ValueError):validate({**self.message(),**change},1000)

    def test_expiry_dismiss_and_old_session_do_not_reopen(self):
        class Window:
            def getWidth(self):return 1920
            def getHeight(self):return 1080
            def addControls(self,*a):pass
            def show(self):pass
            def close(self):pass
        gui=SimpleNamespace(WindowDialog=Window,ControlImage=lambda *a,**k:Mock(),ControlLabel=lambda *a,**k:Mock(),Window=lambda _:Mock())
        with patch.dict(sys.modules,{'xbmcgui':gui}):
            spec=importlib.util.spec_from_file_location('voice_gui_fixture',ROOT/'kodi/voice_display.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        with patch('time.time',return_value=1000):
            d=m.VoiceDisplay();first=self.message();d.receive(first)
            self.assertEqual(d.window.character.pose,'listening')
            d.receive(self.message('responding',1,first['request_id']));self.assertEqual(d.window.character.pose,'speaking')
            d.window.done=True;d.tick();self.assertFalse(d.status['visible'])
            d.receive(self.message('processing',2,first['request_id']));self.assertIsNone(d.window)
            second=self.message();d.receive(second)
        with patch('time.time',return_value=1008):d.tick()
        self.assertEqual(d.status['state'],'expired');self.assertIsNone(d.window)
        with patch('time.time',return_value=1000):
            fresh=m.VoiceDisplay();fresh.receive(self.message('responding',1,first['request_id']))
            self.assertIsNone(fresh.window)
        with patch('time.time',return_value=1001):
            fresh=m.VoiceDisplay();fresh.receive(first);self.assertIsNone(fresh.window)
