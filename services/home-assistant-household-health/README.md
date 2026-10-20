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
At dashboard qualification the required restart surfaced an existing iCloud
authentication failure. A later owner-authorized native cache recovery and
owner-direct verification restored all twenty native entities, six tracker
positions, seven battery readings and five successful calendar queries. Empty
thirty-day calendar results were owner-confirmed. Existing Apple/local-calendar
bindings, calendar bytes, exposure/YAML and pipeline settings remain exact.
The recovery used no Core patch/restart or event/list/media-control write.
See `EdSys-Master/docs/HOME_ASSISTANT_HOUSEHOLD_HEALTH.md` for the current record.

### Native iCloud session recovery — October 20

The October 18 acceptance was superseded by a new native-entry `setup_error`.
Live traces showed Apple's saved-token rejection followed by two-factor
authentication inside device setup, before the normal verification flow could
complete. iCloud3, the independent local Family calendar, AnyList and EdSys Voice
remained loaded. One supported reload of the existing native entry retained the
failure.

Recovery then followed [HA's documented native-session troubleshooting](https://www.home-assistant.io/integrations/icloud/#in-case-of-troubleshooting):
preserve the exact native cache privately, initialize a clean native cache and
reload only the existing entry. The entry-linked verification prompt returned;
one owner-authorized verification completed successfully. No account/password
reset, integration replacement, Core/host/VM restart or repeated authentication
loop was used.

Five actual thirty-day calendar queries and both native to-do reads succeeded.
The selected Family, Home and Reminders sources are available and retain their
reviewed identities. Successful empty calendar responses were distinguished
from a missing response. A loaded entry or a disappearing verification prompt
alone was not treated as acceptance. Two older tracker and two battery records
remain unavailable; retain their honest availability warnings rather than
interpreting those four device states as another account failure. Available
coordinates and battery readings do not prove physical location accuracy.
The upstream library also logged two unsupported date-alarm trigger warnings;
readable reminder items do not qualify every alarm field or future notification.
No Core patch or restart was introduced for that parser limitation.

All prior entity/device bindings, native account data/options, other integration
records, pipelines, exposure, YAML, local-calendar bytes and five private voice
configuration files are preserved. Native rediscovery added one Apple device
with its tracker/battery entities; three existing native battery icons refreshed
as ordinary registry metadata. iCloud3's restore-state file changed through
normal polling, while its configuration and code stayed exact. No manual
device remapping or deletion occurred.

The existing bedroom briefing checks reviewed source identity, permissions and
availability before its local calendar/reminder reads. An unavailable source
raises an explicit error before the empty-results wording; the briefing is not
an LLM tool and its household records remain local. No live display, speech,
calendar/list write, media-control action or OpenAI request was needed for this
repair.

Exact native-cache/configuration archives remain root-private on HA and off-host
on 9950x. Archive SHA checks, tar reads and an isolated two-file native-cache
restore comparison passed. This proves the scoped recovery files, not a full
HA restore. A supplemental encrypted local checkpoint also passed an isolated
verified file restore and hash comparisons. The new offsite copy awaits the
existing mirror schedule; prior offsite backups are retained.
Roll back only the native cache after preserving its current
replacement and reviewing live entry state; never restore whole registries,
configuration or an older HA snapshot over later household edits.

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
