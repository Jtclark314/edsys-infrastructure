# Pi5 voice endpoint

Status, 2026-10-13: software prepared and reboot checked. Microphone/speaker
commissioning and room acceptance are pending. Additional services wait until
voice passes room acceptance and a 24-hour soak.

## Architecture

Keep the healthy Raspberry Pi OS Lite/Debian 13 NVMe base. The Pi was already
minimal, on 1 Gb/s wired Ethernet, with no music service, desktop, failed units
or undervoltage history. Reimaging does not improve microphone acoustics.

Native OHF Linux Voice Assistant **v1.1.15**, commit
`43a183a2322cde4c9e2558f333124941fdeaa439`, runs as dedicated `edsys-voice`.
Twenty-four ARM64 Python 3.13 runtime dependencies have artifact hashes in
`requirements-arm64.lock`. Release metadata replaces upstream placeholders.
PipeWire/Pulse/WirePlumber belong exclusively to this account; the Pi's
operator audio units are masked to avoid competing USB owners. No Docker,
music receiver, Node-RED, MQTT or local LLM is installed.

The Pi owns local `Okay Nabu`/`stop` models, capture and reply playback.
HA on pve-node3 owns Assist, entity exposure and actuation. Its dedicated
`EdSys Living Room Voice` profile uses existing HA Cloud STT/TTS, en-US
`AmberNeural`, and the native HA conversation agent. The preferred household
profile is retained. Speech after wake uses the existing cloud service: this
is not fully offline. General AI conversation/infrastructure control are not
commissioned. The 9950x is management, not a new speech-path dependency.

## Hardware and ports

Recommended: Seeed ReSpeaker XVF3800 **USB 4-Mic Array with case**, without
XIAO. Attach the XMOS USB port to the Pi. The owner selected a small dedicated
speaker. Feed an active speaker from the array's 3.5 mm output so hardware AEC
receives the actual reply playback reference. The attached DragonFly is unused.
Verify actual USB descriptors, firmware and processed capture-channel mapping
before choosing the ASR feed. A deliberate mono PipeWire route may be needed;
do not downmix raw/echo/conference channels or apply an I2S profile to USB.

Stable LVA has a plaintext ESPHome API without transport authentication.
`configure-firewall.py` adds TCP **6053 only from HA 192.168.50.75 on eth0** to
the established firewall. SSH 22 remains restricted to 9950x. LVA uses the
exact Pi LAN bind; the peripheral API is disabled. No public/Tailscale
listener is added. This trusted-LAN boundary replaces the older unimplemented
encrypted-API proposal; it does not prevent LAN observation/spoofing.

## Installation and state

Transfer this directory to the Pi and run `sudo bash install.sh`. The hostname
guard rejects other hosts. Existing private audio configuration is retained;
a new installation stays at `commissioned: false`.

- Program/venv: `/opt/edsys-voice/linux-voice-assistant`
- Private root-owned audio selection: `/etc/edsys-voice/audio.json`, 0640
- Private preferences/models: `/var/lib/edsys-voice`
- Enabled lingering user unit: `/etc/systemd/user/edsys-voice.service`
- Wrapper: `/usr/local/lib/edsys-voice/run.py`

The startup gate requires the exact physical USB microphone and reply sink on
the same device. It rejects output monitors, absent devices and a separate
reply DAC. A presence watchdog stops the process on unplug; restart stays held
until the device is attached. It never chooses a default/HDMI output.
Conversation logging is suppressed. Keep debug logging and household-audio
recording off.

## Hardware commissioning

1. Inventory actual model, firmware, USB path, power and capture channels;
   keep identifiers private. Verify processed-channel isolation, simultaneous
   capture/reply playback, mute, volume without clipping and echo suppression.
2. Select exact source/sink names from `pactl -f json list sources`/`sinks` in
   private `audio.json`; set `commissioned: true` only after audio verification.
3. Restart using the service user's bus:

   ```bash
   voice_uid=$(id -u edsys-voice)
   sudo -u edsys-voice env XDG_RUNTIME_DIR=/run/user/$voice_uid \
     DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$voice_uid/bus \
     systemctl --user restart edsys-voice.service
   ```

4. Add the Pi through HA's native ESPHome integration at its LAN IP/6053,
   assign its room and `EdSys Living Room Voice` profile. Retain other devices
   and the preferred pipeline. Expose only owner-selected entities; keep
   ESPHome service-call access and log subscriptions off.
5. Prove wake, spoken replies, read-only queries and explicitly selected home
   actions. Include TV noise, near/far positions, timers/stop/mute, unplug,
   HA/internet outages and reboot recovery, then a 24-hour soak.

Targets, **not achieved results**: at least 95% wake success over 40 trials;
95% correct intent over 40 owner-relevant commands; no self-triggered replies
or stuck capture/timers after faults; median command-end-to-first-reply under
2 seconds/p95 under 4 seconds; at most one false wake during 8 hours of TV;
clean 24-hour soak. Record actual results before adding minor services.

## Completed software verification

Four audio-gate regressions, dependency consistency, unit/firewall syntax,
native model loading and ESPHome protocol enumeration using an isolated
synthetic audio device passed. The fixture was removed. A Pi reboot restored
one audio owner and the microphone gate, with no failed units or power flags.
Two synthetic questions traversed Cloud STT, native Assist and Cloud TTS with
exact transcripts and fetched reply audio in 0.78/1.70 seconds. These do not
establish human wake, acoustics, audibility or full satellite-to-HA acceptance.
No real household actuator was exercised.

## Recovery

Private pre-change Pi checkpoint: `/var/backups/edsys-voice/pre-20261013`.
HA snapshot: `pre-pi5-voice-20261013`. Prefer deleting only the new profile to
rolling back unrelated HA activity. Copy commissioned audio config/preferences
and firmware mappings into private encrypted backup staging and verify restore
before production promotion. Code/models are rebuildable; backups, identifiers,
credentials and runtime evidence never enter Git/RAG.

For base rollback, stop/disable the voice/audio user units, disable voice
lingering, restore the checkpoint firewall and remove only this deployment's
files after preserving state. Unmask the five operator audio units only if
deliberately restoring that user as audio owner. Remove added packages only
after checking dependency use; avoid broad autoremove. Keep SSH, security
updates, hardware watchdog, SMART, NetworkManager and existing Pi monitoring.

References: [LVA](https://github.com/OHF-Voice/linux-voice-assistant/tree/v1.1.15),
[Seeed USB guidance](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/),
[Seeed FAQ](https://wiki.seeedstudio.com/respeaker_xvf3800_faq/),
[Amazon enclosed USB version](https://www.amazon.com/dp/B0GVJ5YQ58).
