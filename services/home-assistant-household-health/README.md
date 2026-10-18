# Household Health

Builds a responsive, read-only Sections dashboard at `/edsys-health/home` using
Auto-Entities for current availability, missing battery readings, low batteries,
updates and problem/connection reports. Native cards show repair counts,
explicit missing-source checks, the coverage limits and navigation to HA's
existing settings. Deployment and physical acceptance are separate from source
checks. Live voice and playback testing remain held.

Repair counts sit directly below the overview in the first section, keeping
them visible before the potentially long availability lists.

## Bindings and privacy

`build.py` does not contact Home Assistant. A deployment helper supplies reviewed
private bindings; the example IDs below are synthetic:

```json
{
  "repair_sensors": {
    "active": "sensor.active_issues_example",
    "ignored": "sensor.ignored_issues_example",
    "total": "sensor.total_issues_example"
  },
  "watched_entities": ["media_player.endpoint_example"],
  "diagnostic_entities": ["sensor.observation_time_example"],
  "excluded_entities": [
    {"entity": "sensor.retired_example", "reason": "retired"}
  ],
  "battery_threshold": 20,
  "auto_entities_available": true
}
```

Bind Spook's **actual registered** repair sensors, not assumed generated IDs.
Omit unavailable bindings; the dashboard then says not configured. A bound
sensor with no current state says not present, while unknown and unavailable
states remain distinct. Invalid/negative/fractional repair counts do not become
zero. No issue creation, deletion, ignore/unignore or repair action is exposed.

Exclusions require one exact entity and a reason: `disabled`, `retired` or
`intentionally_offline`. Wildcards and contradictory watched/excluded bindings
are rejected. Exclusions are deliberate deployment selections, not a live
registry feed. Revisit them when a source returns to service. Disabled entities
usually have no current state; the automatic lists cannot discover them.
Explicit watched/diagnostic sources allow a missing current state to be shown.

All ordinary current states participate in the dynamic attention lists unless
excluded. This reports entity availability, not a count or physical test of
devices. Off/idle/standby media or lighting states alone are not treated as
failed connections. Problem/connectivity warnings use HA binary-sensor device
class semantics. Update rows include only `update.*` entities reporting `on`.
Their empty state does not establish that every installed component is current.

The generic unknown-state warning and overview omit a bounded set of domains:
`button`, `scene`, `event`, `notify`, `stt`, `tts`, `conversation`, `ai_task` and
`image`. Unknown commonly means never used, no event yet or awaiting an image
for these domains, rather than a failed measurement. The overview counts these
omissions separately and Coverage explains them. Unknown sensors and other
reporting sources still warn. This exception never hides an unavailable state
or an explicit selected-source missing/unknown check.

Battery warnings require a battery-class sensor, `%` unit and a finite numeric
state between 0 and the threshold (1–50, default 20); true zero remains valid.
Binary battery warnings reporting `on` are also included. Unknown, unavailable,
out-of-range, nonnumeric and non-percentage readings stay separate. A sensor
merely named battery is not guessed to report a percentage. No automatic
freshness threshold is imposed on unchanged sensor states; last updated does
not establish a fresh measurement or physical acceptance.

Keep bindings, entity lists, rendered private configurations and native
before/after/readback receipts outside Git/RAG. No source endpoint, credential,
account name or household payload belongs in this folder. The dashboard uses
the signed-in HA user's existing permissions; it adds no listener, service,
cloud AI request, client login, endpoint agent or database.

## Deployment and fallback

Register a storage dashboard using `DASHBOARD_METADATA`; **retain
`require_admin: true`** because diagnostic names/settings are administrative.
The exported config itself does not grant or enforce permissions. Before
native save, install and verify the reviewed Auto-Entities module resource.
Preserve all existing resources, dashboard metadata and system/profile defaults.

```python
from build import build_dashboard, add_navigation

candidate = build_dashboard(private_bindings)
home_candidate = add_navigation(current_home_config, "plans")
```

`add_navigation` selects exactly one existing Sections view, appends one
navigation-only Household Health button and preserves every other card/view.
An exact existing button is idempotent; duplicate, renamed or conflicting links
are refused. It does not choose a dashboard by title or fuzzy entity match.

If Auto-Entities cannot load, native overview/repair/source/coverage cards
remain available. `auto_entities_available: false` deliberately builds native
Markdown lists from the same selectors and current HA templates, with no
custom-card dependency. Restore the reviewed module resource before switching
back. Neither fallback nor an empty list claims that the household is healthy.

All entity rows use native `simple-entity`, preventing special default
switch/button/script/update controls. Taps open HA more-info; holds and double
taps do nothing. Navigation opens settings for an administrator to review.
Loading, refreshing or browsing this dashboard sends no entity service call.
Actions subsequently taken inside native more-info/settings are governed by
the user's HA permissions, outside this dashboard's read-only list behavior.

## Verification and recovery

Run semantic synthetic checks; they use Jinja2 for selection/unknown-state
checks and make no live HA calls:

```sh
python3 -m unittest discover -s services/home-assistant-household-health -p 'test_*.py'
python3 -m py_compile services/home-assistant-household-health/build.py
```

Qualification must separately verify exact native save/readback, module
registration/load, authenticated browser rendering on desktop/phone/tablet
widths, honest no-match/missing messages, and read-only interactions. Do not
toggle entities, issue updates/repairs, create calendar or shopping fixtures, or
perform live voice/playback tests to qualify this page.

October 18 qualification passed ten source checks, all ten generated native
templates and exact native configuration readback. Actual signed-in Nimo Chrome
rendered repair counts, current dynamic rows and empty states without observed
card errors at desktop and CSS 400×862 / 1280×800 device emulation. Physical
phone/Tab A9+ acceptance remains open. Protected calendar, exposure, pipelines,
existing component files and unrelated dashboards/resources were preserved.
The required Core restart surfaced an existing iCloud authentication/2FA failure;
its unavailable data remains visible pending owner-direct sign-in. No account,
entity repair/update, calendar/list write or live voice/playback test was sent.

Before deployment, protect current dashboard, resources and registration
metadata. Re-read immediately before each native save and refuse concurrent
changes. `reconcile_dashboard(current, previous, desired)` changes only owned
sections that actually differ between the private previous/desired configs.
Unchanged sections, unrelated later sections/views and current view metadata
are preserved. A changed owned section must still exactly match the prior
version or the desired version; conflicts are refused. Section identity/layout
migrations need explicit review. Swap previous/desired for narrow recovery.
Do not restore an old whole-dashboard snapshot over later household edits.
Remove only the owned navigation/resource/dashboard change when safe; removing
this page must not delete entities, integrations, calendar data or shopping data.

Backup covers the ordinary native HA dashboard/resource/registration state and
the separately managed installed HACS packages. This service adds no volume or
independent persistent state. Restoring source alone does not restore private
bindings or prove live package/device recovery.

Primary references: [Auto-Entities usage and template rows](https://github.com/thomasloven/lovelace-auto-entities),
[Spook repair sensors](https://spook.boo/repairs/), and
[native simple-entity rows](https://www.home-assistant.io/dashboards/entities/#options-for-entities).
