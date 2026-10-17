#!/usr/bin/env python3
"""Fail closed until an exact microphone/output pair has been commissioned."""
import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

CONFIG = Path('/etc/edsys-voice/audio.json')
ROOT = Path('/opt/edsys-voice/linux-voice-assistant')
STATE = Path('/var/lib/edsys-voice')
AUDIO_LOST_EXIT = 78


def selected_audio(config):
    if not isinstance(config,dict):raise ValueError('Invalid audio commissioning record')
    if config.get('commissioned') is not True:
        raise ValueError('Waiting for microphone commissioning')
    source = config.get('input_device')
    sink = config.get('output_device')
    if not isinstance(source, str) or not source.strip() or source!=source.strip() or source.endswith('.monitor'):
        raise ValueError('An exact physical microphone source is required')
    if not isinstance(sink, str) or not sink.strip() or sink!=sink.strip():
        raise ValueError('An exact reply sink is required')
    identity=config.get('usb_device_identity')
    if (not isinstance(identity,dict) or not {'device.name'}<=identity.keys()
            or not set(identity)<={'device.name','device.serial','device.bus_path'}
            or not {'device.serial','device.bus_path'}&identity.keys()
            or any(not isinstance(v,str) or not v.strip() or v!=v.strip() for v in identity.values())):
        raise ValueError('A stable commissioned USB identity is required')
    sources = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sources'], text=True, timeout=5))
    sinks = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sinks'], text=True, timeout=5))
    if not isinstance(sources,list) or not isinstance(sinks,list):raise ValueError('Invalid audio inventory')
    microphones = [s for s in sources if isinstance(s,dict) and s.get('name') == source
                   and isinstance(s.get('properties'),dict)
                   and s.get('properties', {}).get('api.alsa.pcm.stream') == 'capture'
                   and s.get('properties', {}).get('device.bus') == 'usb'
                   and s.get('monitor_of_sink',4294967295) in (None,4294967295,'4294967295')]
    if len(microphones)!=1:
        raise ValueError('Commissioned microphone is not present')
    outputs = [s for s in sinks if isinstance(s,dict) and s.get('name') == sink
               and isinstance(s.get('properties'),dict)
               and s['properties'].get('api.alsa.pcm.stream')=='playback'
               and s['properties'].get('device.bus')=='usb']
    if len(outputs)!=1:
        raise ValueError('Commissioned reply output is not present')
    if any(device['properties'].get(key)!=value for device in (microphones[0],outputs[0]) for key,value in identity.items()):
        raise ValueError('Microphone and reply output must share the echo-cancelling USB device')
    return source, sink


def watch_audio(config):
    while True:
        time.sleep(5)
        try:
            selected_audio(config)
        except (OSError, ValueError, subprocess.SubprocessError):
            print('Voice endpoint stopped: commissioned audio device disconnected; explicit recovery required.', flush=True)
            # The unit prevents restart for this exit code. Replug must not
            # resurrect an interrupted conversation or select another device.
            os._exit(AUDIO_LOST_EXIT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        config = json.loads(CONFIG.read_text())
        source, sink = selected_audio(config)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Voice endpoint held: commission the microphone/output pair first.')
        return 1
    if args.check:
        return 0
    threading.Thread(target=watch_audio, args=(config,), daemon=True, name='audio-presence').start()
    STATE.mkdir(exist_ok=True)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    # Upstream INFO logging can include STT/TTS content. Silence those loggers;
    # systemd retains only wrapper/startup/failure signals, never conversation text.
    logging.disable(logging.CRITICAL)
    sys.argv = ['linux-voice-assistant',
                '--name', 'Living Room Voice',
                '--network-interface', 'eth0',
                '--host', config['listen_address'], '--port', '6053',
                '--audio-input-device', source,
                '--audio-output-device', 'pulse/' + sink,
                '--music-output-device', 'pulse/' + sink,
                '--audio-input-channels', '1',
                '--mic-auto-gain', '0', '--mic-noise-suppression', '0',
                '--wake-model', 'okay_nabu', '--stop-model', 'stop',
                '--preferences-file', str(STATE / 'preferences.json'),
                '--download-dir', str(STATE / 'models'),
                '--disable-peripheral-api']
    from linux_voice_assistant.__main__ import run
    run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
