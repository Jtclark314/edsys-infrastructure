import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from copy import deepcopy

spec = importlib.util.spec_from_file_location('voice_run', Path(__file__).parents[1] / 'run.py')
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)


class AudioGateTests(unittest.TestCase):
    def pair(self):
        identity={'device.name':'alsa_card.usb-fixture-array','device.serial':'fixture-array-serial'}
        config={'commissioned':True,'input_device':'mic','output_device':'speaker','usb_device_identity':identity}
        mic={'name':'mic','properties':{**identity,'api.alsa.pcm.stream':'capture','device.bus':'usb'}}
        sink={'name':'speaker','properties':{**identity,'api.alsa.pcm.stream':'playback','device.bus':'usb'}}
        return config,mic,sink

    def select(self,config,mic,sink):
        with patch.object(voice.subprocess,'check_output',side_effect=[json.dumps(mic),json.dumps(sink)]):
            return voice.selected_audio(config)

    def test_no_microphone_keeps_endpoint_held(self):
        with self.assertRaises(ValueError):
            voice.selected_audio({'commissioned': False})

    def test_playback_monitor_cannot_become_microphone(self):
        with self.assertRaises(ValueError):
            voice.selected_audio({'commissioned': True, 'input_device': 'speaker.monitor', 'output_device': 'speaker'})

    def test_disconnected_microphone_keeps_endpoint_held(self):
        config,_,sink=self.pair()
        with self.assertRaises(ValueError):self.select(config,[],[sink])

    def test_echo_reference_requires_same_usb_device(self):
        config,mic,sink=self.pair();sink['properties']['device.name']='separate-dac'
        with self.assertRaises(ValueError):self.select(config,[mic],[sink])

    def test_exact_commissioned_usb_capture_playback_pair_passes(self):
        config,mic,sink=self.pair()
        self.assertEqual(self.select(config,[mic],[sink]),('mic','speaker'))

    def test_missing_or_empty_identity_never_matches_by_accident(self):
        for identity in [None,{}, {'device.name':'fixture'}, {'device.name':'','device.serial':'serial'},
                         {'device.name':'fixture','device.serial':' '},
                         {'device.name':'fixture','object.serial':'123'}, {'device.name':'fixture','device.serial':123}]:
            with self.subTest(identity=identity):
                config,mic,sink=self.pair();config['usb_device_identity']=identity
                with self.assertRaises(ValueError):self.select(config,[mic],[sink])
        config,mic,sink=self.pair()
        for dev in (mic,sink):dev['properties'].pop('device.name');dev['properties'].pop('device.serial')
        with self.assertRaises(ValueError):self.select(config,[mic],[sink])

    def test_replaced_device_cannot_claim_old_names(self):
        config,mic,sink=self.pair()
        for dev in (mic,sink):dev['properties']['device.serial']='other-array'
        with self.assertRaises(ValueError):self.select(config,[mic],[sink])

    def test_sink_must_be_physical_usb_playback(self):
        for change in [{'device.bus':'pci'}, {'api.alsa.pcm.stream':'capture'}, {'api.alsa.pcm.stream':None}, {'device.bus':None}]:
            with self.subTest(change=change):
                config,mic,sink=self.pair();sink['properties'].update(change)
                with self.assertRaises(ValueError):self.select(config,[mic],[sink])

    def test_source_must_be_physical_usb_capture_not_monitor(self):
        for change in [{'device.bus':'pci'}, {'api.alsa.pcm.stream':'playback'}, {'api.alsa.pcm.stream':None}]:
            config,mic,sink=self.pair();mic['properties'].update(change)
            with self.assertRaises(ValueError):self.select(config,[mic],[sink])
        config,mic,sink=self.pair();mic['monitor_of_sink']=1
        with self.assertRaises(ValueError):self.select(config,[mic],[sink])

    def test_duplicate_exact_nodes_and_missing_sink_are_held(self):
        config,mic,sink=self.pair()
        for sources,sinks in [([mic,mic],[sink]),([mic],[sink,sink]),([mic],[])]:
            with self.assertRaises(ValueError):self.select(config,sources,sinks)

    def test_reviewed_usb_bus_path_identity_is_supported(self):
        config,mic,sink=self.pair();identity={'device.name':'alsa_card.usb-fixture-array','device.bus_path':'platform-fixture-usb-0:1'}
        config['usb_device_identity']=identity
        for dev in (mic,sink):dev['properties'].update(identity);dev['properties'].pop('device.serial')
        self.assertEqual(self.select(config,[mic],[sink]),('mic','speaker'))
        sink['properties']['device.bus_path']='platform-fixture-usb-0:2'
        with self.assertRaises(ValueError):self.select(config,[mic],[sink])

    def test_disconnect_then_exact_replug_revalidates_without_default_device(self):
        config,mic,sink=self.pair()
        self.assertEqual(self.select(config,[mic],[sink]),('mic','speaker'))
        with self.assertRaises(ValueError):self.select(config,[],[])
        substitute=deepcopy(sink);substitute['name']='default-speaker'
        with self.assertRaises(ValueError):self.select(config,[mic],[substitute])
        self.assertEqual(self.select(config,[mic],[sink]),('mic','speaker'))

    def test_malformed_inventory_is_held(self):
        config,mic,sink=self.pair()
        for sources,sinks in [({},[sink]),([mic],{}),([{'name':'mic','properties':None}],[sink])]:
            with self.assertRaises(ValueError):self.select(config,sources,sinks)

    def test_disconnect_watchdog_uses_nonrestarting_exit(self):
        config,_,_=self.pair()
        with patch.object(voice.time,'sleep') as wait,patch.object(voice,'selected_audio',side_effect=ValueError('fixture missing')),\
                patch.object(voice.os,'_exit',side_effect=SystemExit) as terminate,patch('builtins.print'):
            with self.assertRaises(SystemExit):voice.watch_audio(config)
        wait.assert_called_once_with(5);terminate.assert_called_once_with(78)
        unit=(Path(__file__).parents[1]/'edsys-voice.service').read_text()
        self.assertIn('RestartPreventExitStatus=78',unit)
