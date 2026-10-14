# Bedroom Pi Plex dashboard

Native Home Assistant Media view `/edsys-media/bedroom-pi`, titled **Bedroom Pi
Plex**. It controls Kodi/PM4K on the Pi over HA's standard Kodi integration;
LG native Plex remains separate. Navigation links are added to Media overview
and Master Bedroom without replacing existing cards or changing other views.

The page provides current playback/progress, play/pause/stop, skip back/forward,
Open Plex, Now playing, arrows/OK/Back/Home/Info/Options, physical HDMI 1 selection
and LG/soundbar volume. Open Plex and browse on the TV with the on-page pad.
Skipping follows Kodi's configured step sizes; no fixed seconds are promised.
The existing native LG remote remains linked. No frontend package/resource is
added. Controls have explicit endpoint targets; native HA permissions apply.

## Connection and privacy

Kodi HTTP on port 8080 uses a newly generated endpoint-only Basic credential.
The Pi firewall admits that port only from the reviewed HA IPv4 on eth0;
loopback remains allowed. This is authenticated HTTP within a trusted LAN,
not transport encryption. TCP/WebSocket 9090 and discovery remain disabled;
HA uses native HTTP polling, so displayed state can lag its polling interval.
No HA administrator credential is installed on the Pi. Nimo voice, the Pi voice
gate and the proposed presentation-event transport are unchanged.

Runtime host/ports/state: existing HA on node3, existing Pi Kodi profile and
`/etc/nftables.conf`. Native HA integration stores endpoint credentials. Pi
credential file `/etc/edsys-bedroom-kodi-control.json` is root-private; Kodi's
private profile also holds its settings. Private hub credentials, bindings,
flow state and dashboard baselines remain outside Git/RAG. Do not print the
native create-entry data or media URLs/account state.

## Prepare and deploy

Serialize Pi changes and compare current bindings before dashboard saves.
Back up and stop only the display unit first; graceful stop preserves PM4K
progress. No HA restart is needed. The root Pi helper refuses an already-enabled
control profile and adds only its exact live firewall rule rather than flushing
other rules. The persisted firewall retains every existing rule.

```bash
# On the reviewed Pi, with display stopped:
sudo python3 PRIVATE_SOURCE/pi/enable-ha-control.py --ha-address HA_IPV4 --apply
sudo systemctl start edsys-bedroom-display
# Transfer the newly generated endpoint credential through existing SSH into a
# 0600 file under a private hub directory; never place it in arguments/source.
python3 services/home-assistant-pi-plex/connect.py --host PI_IPV4 \
  --credentials PRIVATE_CREDENTIAL_JSON --private-state PRIVATE_RECOVERY --apply
```

The native flow may time out probing its default, blocked WebSocket port.
Do not repeat a creation or credential submission after an unknown outcome.
`--resume` reads the saved flow's current native form first, then continues that
step. The qualified configuration sets the optional WebSocket port to zero
(HTTP only). If an entry already exists, inspect/bind it rather than creating a
duplicate. Preserve the private flow recovery record.

Review the new native entity/device against the Pi integration, name/locate it
in Master Bedroom, then create private binding JSON with `player` and `tv`, each
containing `entity_id`, `device_id`, `config_entry_id`. The deployer validates
platform/identity, builds private before/candidate files, checks for concurrent
edits and reads back the exact saved dashboard. Existing Pi views are refused.

```bash
python3 services/home-assistant-pi-plex/deploy.py --binding PRIVATE_BINDING \
  --private-state PRIVATE_RECOVERY
python3 services/home-assistant-pi-plex/deploy.py --binding PRIVATE_BINDING \
  --private-state PRIVATE_RECOVERY --apply
python3 -m unittest discover -s services/home-assistant-pi-plex -p 'test_*.py'
```

## Verified acceptance: 2026-10-14

Exact dashboard readback passed. The actual signed-in Nimo browser rendered
current episode/progress at desktop and narrow widths. Clicked Play/Pause
changed actual Pi speed 0/1; Skip Back changed position; Info/Back displayed/
dismissed the on-TV info layer. The test position was restored and playback
resumed. Jeremy then independently confirmed the page’s playback controls work.
Three focused preservation/target/firewall checks and five LG regressions passed.
Anonymous RPC returned 401; authenticated RPC passed. Live/persistent firewall
restriction and HTTP-only listeners were verified.

## Verify and recover

Verify actual Pi player identity/title/position, authenticated RPC and anonymous
401, source-restricted firewall, dashboard save/readback and desktop/phone layout.
Exercise pause/play and a bounded seek/navigation action through the actual page,
with Pi player state readback. A successful HTTP/service response alone is not
proof the TV reacted. Preserve/restore the test position, volume and play state.
Do not issue an unrelated media selection or replay uncertain commands.

Recovery: remove only the `bedroom-pi` view and its two navigation links from the
latest dashboard, preserving later edits. Unload/remove only the newly created
Kodi entry if rolling back the connection. With display stopped, compare and
restore only HTTP settings from the private `ha-control-*` checkpoint, remove
only the commented Kodi HTTP rule from the live/persisted firewall and retain
SSH/voice rules. Do not overwrite the current linked Plex profile or regress
watch progress. Keep native LG Plex available independently.

Owner: Jeremy. Architecture: EdSys-Master `docs/PI5_EDGE_LIVINGROOM.md` and
`docs/NIMO_HA_VOICE.md`. [Native Kodi integration](https://www.home-assistant.io/integrations/kodi/).
