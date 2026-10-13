import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('voice_run', Path(__file__).parents[1] / 'run.py')
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)


class AudioGateTests(unittest.TestCase):
    def test_no_microphone_keeps_endpoint_held(self):
        with self.assertRaises(ValueError):
            voice.selected_audio({'commissioned': False})

    def test_playback_monitor_cannot_become_microphone(self):
        with self.assertRaises(ValueError):
            voice.selected_audio({'commissioned': True, 'input_device': 'speaker.monitor', 'output_device': 'speaker'})

    def test_disconnected_microphone_keeps_endpoint_held(self):
        with patch.object(voice.subprocess, 'check_output', side_effect=['[]', '[]']):
            with self.assertRaises(ValueError):
                voice.selected_audio({'commissioned': True, 'input_device': 'mic', 'output_device': 'speaker'})

    def test_echo_reference_requires_same_usb_device(self):
        mic = {'name': 'mic', 'properties': {'api.alsa.pcm.stream': 'capture', 'device.bus': 'usb', 'device.name': 'array'}}
        sink = {'name': 'speaker', 'properties': {'device.name': 'separate-dac'}}
        with patch.object(voice.subprocess, 'check_output', side_effect=[json.dumps([mic]), json.dumps([sink])]):
            with self.assertRaises(ValueError):
                voice.selected_audio({'commissioned': True, 'input_device': 'mic', 'output_device': 'speaker'})
        sink['properties']['device.name'] = 'array'
        with patch.object(voice.subprocess, 'check_output', side_effect=[json.dumps([mic]), json.dumps([sink])]):
            self.assertEqual(voice.selected_audio({'commissioned': True, 'input_device': 'mic', 'output_device': 'speaker'}), ('mic', 'speaker'))
