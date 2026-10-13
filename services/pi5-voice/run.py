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


def selected_audio(config):
    if config.get('commissioned') is not True:
        raise ValueError('Waiting for microphone commissioning')
    source = config.get('input_device')
    sink = config.get('output_device')
    if not isinstance(source, str) or not source or source.endswith('.monitor'):
        raise ValueError('An exact physical microphone source is required')
    if not isinstance(sink, str) or not sink:
        raise ValueError('An exact reply sink is required')
    sources = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sources'], text=True, timeout=5))
    sinks = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sinks'], text=True, timeout=5))
    microphones = [s for s in sources if s['name'] == source
                   and s.get('properties', {}).get('api.alsa.pcm.stream') == 'capture'
                   and s.get('properties', {}).get('device.bus') == 'usb']
    if not microphones:
        raise ValueError('Commissioned microphone is not present')
    outputs = [s for s in sinks if s['name'] == sink]
    if not outputs:
        raise ValueError('Commissioned reply output is not present')
    if microphones[0]['properties'].get('device.name') != outputs[0].get('properties', {}).get('device.name'):
        raise ValueError('Microphone and reply output must share the echo-cancelling USB device')
    return source, sink


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
    def watch_audio():
        while True:
            time.sleep(5)
            try:
                selected_audio(config)
            except (OSError, ValueError, subprocess.SubprocessError):
                print('Voice endpoint stopped: commissioned audio device disconnected.', flush=True)
                os._exit(1)
    threading.Thread(target=watch_audio, daemon=True, name='audio-presence').start()
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
