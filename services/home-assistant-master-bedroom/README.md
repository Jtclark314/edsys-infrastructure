# Master Bedroom Home Assistant dashboard

Native Home Assistant sidebar dashboard **Master Bedroom**, with entry point
`/edsys-master-bedroom/home`. Deployed on 2026-11-01 from live entities and the
existing dashboards. Exact native readback, preservation of existing dashboards,
and signed-in native browser verification passed.

## Contents and ownership

The dashboard has twelve native views: Home, TV remote, Lights & switches,
Voice & status, and copies of all eight existing Bedroom Pi views. Those copies
retain Player/Plex, YouTube, Music & books, Night & sleep, TV screen, Household,
LG remote and Capabilities. Internal navigation points to this dashboard;
the original Bedroom Pi and Media dashboards remain available separately.
LG app/remote controls target the TV; Kodi/Plex controls target the Pi on HDMI 1.
Existing native Music Assistant and other app launchers are reused.

Every observed HA light and switch is listed individually: five lights and
72 switches at preparation. Room controls precede device/camera settings;
entity-list header toggles are disabled. Unavailable devices remain visible,
including the existing bedside color light and floodlight. No device was
repaired or omitted to make the dashboard appear healthy.

Voice opens the existing production EdSys Assist pipeline for typing or
microphone use and shows actual service/backend status. HTTPS or the HA app is
required for browser microphone recording. Existing Nimo Alt+V remains the
desktop voice route. Service state alone does not prove microphone/speaker
operation. Existing capabilities and feature limits remain visible.

This is maintainable native storage-mode Lovelace configuration, editable in
Home Assistant. The deployed device list and copied Pi views are snapshots;
future devices or changes in the original Pi dashboard require deliberate
updates through the native dashboard editor. No automatic synchronization,
automation, frontend resource, integration or dependency is added.

## Preparation and deployment

`build.py` accepts a private live observation and validates entity, service and
view-link references. The observation contains existing dashboard copies,
entity/registry/area/service data and reviewed media/voice bindings. Runtime
configuration, household content, pipeline identifiers, private bindings,
before/candidate/readback files and recovery state stay outside Git and RAG.
The source embeds no live entity identities or account data.

`deploy.py` is create-only. It refuses an existing matching dashboard or an
earlier recorded apply attempt. Before creating, it compares every original
dashboard, metadata/resources and the exact light/switch inventory with the
reviewed observation. It saves through the existing protected native API,
requires exact new-dashboard readback, and verifies every original dashboard
and resource again. It sends no household control commands and restarts no
service. After an uncertain write, inspect native state and the private apply
record; never rerun blindly.

```bash
python3 services/home-assistant-master-bedroom/deploy.py \
  --private-state PRIVATE_RECOVERY
python3 services/home-assistant-master-bedroom/deploy.py \
  --private-state PRIVATE_RECOVERY --apply
```

Use the existing [HA control route](../../docs/HOME_ASSISTANT_CONTROL.md).
No credentials, permissions, listeners or authentication policies change.

## Verification and recovery

On 2026-11-01 exact native readback preserved all sixteen original dashboard
configurations/metadata and frontend resources. The observed 77 individual
light/switch rows and 158 original Pi action payloads were retained with no
missing references. All twelve views rendered in Nimo's actual signed-in HA
frontend with no visible entity/configuration errors. Desktop checks and selected
411-pixel phone-width checks passed; this is browser layout verification, not
physical-phone acceptance. TV remote remains a main tab, and the voice page
showed EdSys backend Ready. No household action or physical-control test was
sent; microphone, speaker, media and device acceptance remain separate.

The main and Master Homepage launchers are deployed. Complete API parity and
rendered title/layout checks passed for 82 and 206 cards, respectively. Browser
inspection found the exact named card, description and destination in both.
Following that destination while signed out reached HA's sign-in boundary.
Only the two Homepage launcher containers were refreshed; this work restarted
neither HA nor the Pi. Read-only rollback preview also passed without deletion.

Require exact native readback, unchanged original dashboard/resource checks,
complete live light/switch coverage, and valid referenced entities/services/
links. Open the actual signed-in Home Assistant page on desktop and narrow
phone layouts; inspect every view for missing/configuration errors and clear
navigation. Read-only dashboard verification does not establish physical
remote, playback, microphone, speaker or household-device acceptance.
Verify the new launcher in both EdSys Homepage and Master Dashboard after
their normal scoped source build/check/deployment.

Rollback previews removal of only this newly created dashboard and refuses
later metadata/configuration edits. Review those edits before any removal.

```bash
python3 services/home-assistant-master-bedroom/deploy.py \
  --private-state PRIVATE_RECOVERY --rollback
python3 services/home-assistant-master-bedroom/deploy.py \
  --private-state PRIVATE_RECOVERY --rollback --apply
```

After authorized rollback, remove only the associated Homepage launcher through
its normal generator/deployer. Do not restore all HA storage, original
dashboards or resource lists. Original dashboard access is retained throughout.

Current-state summary: EdSys-Master
`docs/obsidian/services/Home Assistant/Dashboards - RAG Summary.md`.
