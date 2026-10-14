# Master Bedroom LG remote

Native Home Assistant dashboard extension and the optional Nimo keyboard window.
Runtime dashboards and reviewed TV registry bindings remain private. This is a
frontend extension of existing HA, not a new service/container or integration.

## Operation

Open **Media → Master Bedroom**. Native buttons provide power on/off, D-pad/OK,
Home/Back/Exit/Settings, volume/mute, playback, app shortcuts, numbers, channel
functions and colored keys. The original TV, apps/inputs, volume slider and
paired Plex session cards are retained. Navigation is hidden while HA reports
the TV off/unknown/unavailable; Wake TV remains available through the existing
Ethernet wake automation. App shortcuts are limited to an explicit shortlist
and current source availability. No button automatically launches at page load.

The stable **HDMI 1 · Pi** shortcut addresses physical `HDMI_1` with native
`webostv.command` / `tv/switchInput`, independent of a mutable source label.
The LG currently calls that port **PC** in its native input menu. Home, Spaces
and the Media overview have a native source selector and adjacent Pi button.
HDMI 2/ARC is unchanged. The shortcut selects the Pi input; the LG D-pad still
controls the LG and does not imply Kodi CEC navigation.

`repair_hdmi1.py` repairs existing dashboards rather than redeploying the whole
remote. It validates the private registry binding, saves private baselines and
candidates, compares again before save and verifies exact readback. See its
`--help` for private path arguments. Preserve later edits during rollback.

Navigation uses the built-in `webostv.button`; rewind/fast-forward use fixed
`media.controls/rewind` and `media.controls/fastForward` commands, avoiding the
native next/previous action's Live TV channel behavior. The TV's current app
decides how a key behaves. Channel/numeric functions depend on that app or tuner.
The cloud voice pilot and its allowed tools are unchanged.

On Nimo, **Master Bedroom TV Remote** opens a separate window using the existing
private HA sign-in. Click **Keyboard remote** to focus its native button, then
use arrows, Enter, Escape, Home or digits. Clicking the page releases the keys.
Only this focus control consumes these keys; Alt+V remains with Nimo Voice.
The keyboard window denies microphone access and exits when closed. Opening
its shortcut again restores the existing remote window rather than duplicating it.

Keyboard input checks the exact HA route, modal dialogs, connected socket,
reviewed entity/device/config-entry/area identities and current TV availability.
Held keys and concurrent keys are dropped, not queued. Blur/route change and
expired preflight reads cannot dispatch a delayed key. A service timeout or
failure after dispatch is uncertain and is not automatically retried. An
acknowledged key means submitted, not that the TV's cursor was independently
observed. The async WebView result returns over the existing same-origin
message channel with a per-request correlation ID.

## Deploy and recover

Use existing owner authority and HA operator access; no provider key, token,
listener, port, HACS package, frontend resource or HA restart is required.
Binding JSON contains only the approved `entity_id`, `device_id`,
`config_entry_id` and effective `area_id`, outside Git/RAG.

```bash
python3 services/home-assistant-lg-remote/deploy.py \
  --binding PRIVATE_BINDING --private-state PRIVATE_RECOVERY
# Review the private candidate and exact native registry/area mapping.
python3 services/home-assistant-lg-remote/deploy.py \
  --binding PRIVATE_BINDING --private-state PRIVATE_RECOVERY --apply
```

The deployment captures/compares the complete dashboard baseline, rejects
duplicate extensions, rechecks concurrent edits and reads back the exact save.
It sends no household action. Preserve later dashboard changes when rolling
back: remove only the added sections from the latest configuration, retaining
the original TV/Plex cards and every other view. Private full baselines support
recovery but should not overwrite intervening edits.

For Nimo, follow `../nimo-voice/README.md` and install the reviewed private
`lg-remote.json` in the existing user-private app directory. Preserve the
binary/scripts before replacing the owned client. The existing profile and
settings are reused, never exported. The remote shortcut runs `NimoVoice.exe
--remote`; its mutex/status file are separate from voice. Stop only these owned
clients before replacing the executable. Keep Startup pointing to ordinary voice.
The private `recovery/lg-remote-20261014` baseline supports narrow rollback;
remove only the owned remote shortcut and restore the previous binary/scripts.

## Qualification

```bash
python3 -m unittest discover -s services/home-assistant-lg-remote -p 'test_*.py'
node services/nimo-voice/test-lg-key.cjs
node --check services/nimo-voice/lg-key.js
```

Five dashboard and fourteen keyboard regressions cover preserved original
views/cards, bounded targets, duplicate refusal, wrong/offline route, identity/
room drift, dialogs, blur, expiry and no replay on failure. Native C# compilation,
PowerShell parsing and pinned SDK publisher checks apply to the Windows client.
Live verification and physical acceptance are recorded separately in the owning
current-state record. Temporary keyboard probes must prevent dispatch, then be
removed with production-script digest readback before user handoff.

Host/runtime: existing HA on node3 and signed-in Nimo user. Ports/volumes:
unchanged HA and existing private WebView profile; no additional daemon.
Backup: private dashboard and Nimo binary/script baselines. Monitoring: native
HA availability and separate generic Nimo `remote-status.json`; no transcript.

References:
- https://www.home-assistant.io/actions/webostv.button/
- https://www.home-assistant.io/integrations/webostv/
- EdSys-Master `docs/NIMO_HA_VOICE.md` and the reviewed dashboard summary.
