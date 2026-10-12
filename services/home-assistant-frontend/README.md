# Home Assistant Frontend Initialization Guard

This is one local Lovelace module resource, not a service, theme, custom media
card, or Home Assistant package upgrade. It keeps native media cards and their
layouts, playback controls, artwork, and progress behavior.

## Scope and cause

The owner authorized repair of the media-card initialization warning on
2026-10-11. Core 2026.10.1 uses frontend 20260930.3. Its native media-card
`_stateObj` getter reads `hass.states[_config.entity]` before `_config` exists
when the progress controller runs during lazy custom-element upgrades.

The guard matches only that exact affected getter shape. Until both `hass.states`
and the entity configuration exist, it returns no state; afterward it delegates
to the original getter. It does not catch unrelated errors, filter the console,
invent state, call a service, or modify the installed frontend distribution.

If the native class is not loaded, the module briefly wraps registration for
`hui-media-control-card` so the getter is guarded before connected elements
upgrade. All other registrations are forwarded unchanged. The original
registration function/descriptor is restored immediately for the target and
on loader failure; a concurrent resource's wrapper is never overwritten.
Native card helpers load the class without attaching a bootstrap card.
An upstream-fixed or structurally changed getter is left untouched. Reverify
and remove this workaround after a future normal frontend upgrade.

## Runtime and deployment

- Protected operator route: [Home Assistant Control](../../docs/HOME_ASSISTANT_CONTROL.md).
- Current reviewed resource version: `v1.0.1`.
- Runtime asset: `/config/www/edsys-media-control/v1.0.1/media-control-init-guard.js`.
- Module URL: `/local/edsys-media-control/v1.0.1/media-control-init-guard.js`.
- Source: `media-control-init-guard.js` in this directory.

Before an authorized deployment, privately save native resource metadata and
fingerprints of dashboards, automations/scenes, registries, iCloud3 configuration
and the installed native frontend. Atomically stage the source at a new
versioned URL; refuse an unexpected existing asset. Verify its SHA-256 through
both the guest filesystem and ordinary HTTP response, then register only this
module through `lovelace/resources/create`. Later revisions update only its
resource URL through `lovelace/resources/update`, preserving the resource ID
and all unrelated resources. Reload the browser; no HA restart is required.

The first `v1.0.0` attempt guarded only after native definition and therefore
arrived too late on cold loads. It is not enabled. Version `v1.0.1` adds the
pre-upgrade guard; retain the disabled prior asset as private recovery evidence.
Do not overwrite an immutable URL or edit frontend/vendor bundles directly.

## Checks and rollback

```sh
npm test
node --check media-control-init-guard.js
```

Seventeen regressions cover the original failure, initialization order,
current state/progress updates, missing entities, descriptor/idempotence,
upstream-fixed getters, unrelated errors, pre-definition upgrades, registration
restoration, other tags/options, loader failure and concurrent wrappers.
An isolated Playwright fixture exercises real browser custom-element upgrades
with synthetic state and confirms the original failure and corrected behavior.
It is not a physical media-command test or an authenticated acceptance bypass.

Acceptance additionally requires fresh signed-in browser loads of affected
views, clear console checks, rendered controls/artwork where media is active,
responsive framing, and readback that protected configuration/native assets
remain unchanged. Playback/volume/power commands are not needed for this repair.

For an authorized rollback, read the current resource list and compare the
guard's resource ID/URL with its private checkpoint. Remove only that resource
through `lovelace/resources/delete` and reload clients. Leave dashboards,
media integrations, other resources and installed frontend files untouched.
The original native warning will return while the affected frontend remains.
Full runtime evidence and recovery metadata belong outside Git/Obsidian/RAG.

Primary references:
- [Affected native card source](https://github.com/home-assistant/frontend/blob/20260930.3/src/panels/lovelace/cards/hui-media-control-card.ts)
- [Progress controller source](https://github.com/home-assistant/frontend/blob/20260930.3/src/common/controllers/media-progress-controller.ts)
- [Local frontend resources](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/)
