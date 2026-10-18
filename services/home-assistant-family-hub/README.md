# Home Assistant Family Hub

Source for a responsive family calendar and selected shopping lists, using the
existing authenticated HA frontend. Operational state is separate from source
checks: **AnyList authentication, isolated-list round trips and the final
owner-selected grocery-list binding are verified in signed-in Nimo HA.**
Physical phone/tablet acceptance remains pending; live voice/playback/TV tests
remain held.

### Verified connector progress — October 17, 2026

Final native setup selects only the owner-selected grocery list, with
meal-calendar export off and 60-second polling. Its one to-do entity and two
settings selects are explicitly unexposed. Qualification used an isolated test
list. Seven live HA→AnyList cases passed: comma-preserving add, check/uncheck, rename,
rename while completed, completed-name add reusing its UID, and UID deletion
with an empty-list readback. Read-only waits observed 6–10-second convergence;
early cached reads were not treated as proof or a reason to replay writes.

An independently web-created fixture reached HA in 19 seconds; a subsequent
web quantity/note change reached HA in about 23 seconds. The actual Hub shopping
card rendered the same fixture and quantity/note without changing the frontend
asset. Its checkbox completed the same UID while retaining quantity/note;
two-line entry created separate items and retained a comma within an item.
Independent AnyList web and HA readback confirmed the resulting items and
completed rename with metadata preserved. All three owned fixtures were
UID-deleted; the test list was verified empty in HA and AnyList web.

Only entity/display-title settings in two Hub shopping cards changed for the
final grocery binding; exact native readback preserved all other Hub fields.
Nimo rendered the final heading, enabled checkboxes, successful-read time,
Refresh and Open AnyList controls without setup pending. The regular grocery
list received **no item mutations** during this final read-only verification.
Keep account/list names, IDs and test contents in private receipts. See the
[operational checkpoint](../../docs/HOME_ASSISTANT_DASHBOARDS.md#family-hub-2026-10-17).

## Components and boundaries

- `build.py` creates the new `edsys-family` dashboard with Home, Calendar,
  Shopping and Help views. `add_navigation` adds one navigation-only button to
  an explicitly selected existing Sections view without replacing its cards.
- `upgrade_calendar_agendas` narrowly upgrades an existing Hub using Calendar
  Card Pro while preserving the native month calendar and every unrelated
  setting. Use this patch for an existing dashboard, not a fresh builder output.
- `family-hub-card.js` registers `custom:edsys-family-event-card` and
  `custom:edsys-family-shopping-card`. Cards use HA's supplied `hass` session,
  native WebSocket/service APIs and calendar readback; there is no separate
  login, token field, server, listener or cloud AI integration.
- The EdSys quick-add and shopping cards add no browser-storage persistence.
  Their drafts, list responses and uncertain write locks live only in each card
  instance's RAM. Ordinary `hass` updates preserve drafts/focus; navigation,
  reload or card recreation can discard them. After an uncertain save, inspect
  the backend before re-entering the draft. Calendar Card Pro separately caches
  calendar events in that browser's local storage, as described below.

HA permissions remain the actual authorization boundary. A fixed calendar and
selected list IDs constrain these cards' UI; they do not restrict the rest of a
user's HA account. Content is rendered as text, not injected provider HTML.
Keep real bindings, events, shopping content, credentials, diagnostics and
before/after dashboard JSON private and outside Git/RAG.

## Configuration and use

The builder accepts private bindings with this shape; IDs below are examples:

```json
{
  "calendar": "calendar.family_example",
  "shopping_entities": [],
  "allow_recurrence": false
}
```

One explicit `calendar.*` is required. Configure a writable native HA calendar
with the intended household access and verified home time zone. Set
`allow_recurrence` only after qualifying that calendar's recurrence support.
Shopping accepts at most twelve distinct, explicit `todo.*` IDs. Leave it empty
until the intended AnyList lists are authenticated and selected; this displays
setup pending rather than substituting another list or claiming an empty list.

From a private deployment helper with this service on its Python import path:

```python
from build import build_dashboard, add_navigation

candidate = build_dashboard(private_bindings)
home_candidate = add_navigation(current_home_config, existing_view_path)
```

The builder returns configuration, not dashboard registration or a deployment.
Publish the reviewed JavaScript as a versioned HA module resource before using
its cards. Preserve existing resources and latest dashboard baselines, compare
for concurrent edits before native saves, and require exact saved readback.
Use normal HA sign-in and the existing qualified remote-access route on phones.
The external Open AnyList link requires its own AnyList sign-in/entitlement.

### Calendar Card Pro agendas

The agenda settings are reviewed against **Calendar Card Pro 4.2.0**, tag commit
`7816003d3547096b83f92c8baacc81e7f20c57f4`, and its tagged
[configuration reference](https://github.com/alexpfau/calendar-card-pro/blob/v4.2.0/docs/reference/configuration.md)
and [expand action](https://github.com/alexpfau/calendar-card-pro/blob/v4.2.0/docs/features/actions.md).
The exact tagged [configuration/types](https://github.com/alexpfau/calendar-card-pro/blob/v4.2.0/src/config/types.ts),
[card implementation](https://github.com/alexpfau/calendar-card-pro/blob/v4.2.0/src/calendar-card-pro.ts)
and [event/cache implementation](https://github.com/alexpfau/calendar-card-pro/blob/v4.2.0/src/utils/events.ts)
were checked rather than relying on the latest documentation alone.
Install the reviewed public release through HACS and register its module before
publishing a candidate. Retain both `calendar-card-pro.js` and its sibling
`editor.js`; HACS manages those files. This source does not install packages,
register resources or contact HA. Keep installed versions/hashes and dashboard
before/after snapshots in private deployment evidence.

- **Home:** seven-day agenda initially shows up to six upcoming events. Its title
  explicitly offers expansion/collapse; tapping the card or pressing Enter/Space
  while it is focused toggles the whole seven-day range. No remaining-event
  indicator is assumed. **More dates & calendar** is a separate native
  navigation button to the richer Calendar view.
- **Calendar:** an uncapped fourteen-day agenda, the unchanged native month
  calendar and existing quick-add form. **Open calendar editor** opens HA's full
  native calendar panel; select the household calendar there when necessary.
  Existing events remain editable/deletable through native HA, not the agenda.
  The native month card and editor button remain available independently if
  the third-party card cannot load; there is no automatic fallback switch.
- Both agendas use readable 16px event titles and 14px times/locations, natural
  card height, wrapping text and the existing responsive Sections layout for
  phones and the 11-inch tablet. They display only the one supplied calendar,
  with no weather provider, description expansion, external calendar service,
  event mutation or AI route. Local HA remains the calendar's authority.
- The one-minute refresh setting is in **minutes**, not seconds. Reads are
  cache-aware; `refresh_on_navigate` is enabled but is not a guarantee that every
  view navigation bypasses a valid cache. A newly added date may take a refresh
  to appear in this third-party display; the add form's existing native readback
  remains the save confirmation. Empty data does not hide the card: the normal
  localized empty-state row remains visible. A failed initial read shows the
  upstream error state; a later failed refresh can retain previously read events.
- Upstream 4.2.0 stores raw calendar events in browser `localStorage` and has no
  supported switch to disable that persistence. With these settings, nonempty
  cache entries are usable for one minute; empty entries have a fifteen-second
  lifetime. Expiry controls cache reuse, not a promise of immediate physical
  deletion. The events remain in HA and on the signed-in device; this creates no
  external calendar or AI route. The RAM-only draft guarantee applies to the
  EdSys forms, not this third-party display's event cache.
- Agenda day boundaries and times follow the device/browser time zone in this
  upstream version; there is no card setting to force HA's home time zone. The
  new Calendar section explains that distinction. The existing add form still
  uses the verified HA home time zone and retains all DST/recurrence guards.

For an **existing** Hub, use the latest native baseline and its already verified
private Local Calendar binding:

```python
from build import upgrade_calendar_agendas

candidate = upgrade_calendar_agendas(current_family_config, private_calendar_id)
```

The pure patch targets only the Home **The next seven days** section and the
Calendar **Family calendar** location. It replaces the recognized native Home
`listWeek` card, retains its common HA visibility/layout options, adds the Home
navigation button when absent, and inserts **Upcoming family dates** immediately
before the existing native month section. Root dashboard fields, view settings,
all other cards/views, the exact month card, shopping bindings and quick-add
recurrence/DST behavior remain unchanged. An already upgraded Hub is returned
unchanged, preserving later agenda presentation edits. Duplicate/missing owned
locations, unexpected bindings, partial upgrades and unfamiliar legacy card
customizations are refused for explicit review instead of being overwritten.

Before saving, compare the live configuration with the captured baseline for
concurrent edits. Check the intended diff and exact native readback afterward.
Verify real resource loading, empty/error presentation and expansion through a
signed-in HA browser at desktop, tablet and phone widths; synthetic source
checks alone do not prove a production custom card or physical-device behavior.

October 18 qualification used the actual signed-in Nimo browser: both agendas,
preserved month calendar/forms and native-editor navigation rendered correctly.
Chrome device emulation at 400×862 and 1280×800 showed single-column phone and
two-column tablet layouts without card errors. Empty Home agenda expansion and
collapse worked; no event/list fixture was written. This does not establish
physical phone/Tab A9+ acceptance. Native calendar bytes and protected existing
configuration were retained; live voice and playback testing stayed held.

### Calendar behavior

- The add form targets only its configured calendar. Editing/deleting existing
  dates uses the full native calendar UI; entries do not automatically appear
  in a phone's separate Calendar app.
- All-day entries cover one date with an **exclusive next-day end**. Timed
  entries use HA's home time zone, independently of the browser's local zone,
  and accept 1–1440 elapsed minutes. Unsupported/ambiguous/skipped DST endpoints
  are refused rather than guessed.
- Optional daily, weekly, monthly or yearly recurrence is finite: 2–52
  occurrences. Missing month/year dates are skipped. A recurring first event
  crossing a DST clock change is refused; later occurrences follow the native
  calendar's recurrence semantics, with wall-clock duration caveats shown in UI.
- A submission makes one create attempt. Acknowledgement and newly visible
  matching calendar readback are distinguished. Check calendar again is read
  only. Uncertain or unconfirmed results retain the current draft and require
  explicit review; reconnect never automatically resends the write. This is
  not durable server-side deduplication across reloads or different clients.

### Shopping behavior

Select one configured list explicitly. Add one item per line, retaining commas
inside an item; a batch allows 30 lines of at most 250 characters each. Adds are
sequential and stop at the first uncertain result. Acknowledged lines leave the
draft; the uncertain line and unattempted lines remain for review. Refresh reads
the list without replaying additions. Completion/uncompletion addresses a read
item UID, confirming the exact selected entity, UID and desired status from
backend readback. A stale or missing target remains pending, blocks further
writes and permits explicit read-only refresh; it never reports confirmed
completion merely because a refresh returned. Missing/malformed capability
flags disable mutations.

Unavailable data is not a successful empty list. The UI shows its last
successful read and retains prior items when refresh fails. After uncertainty,
check the actual list before acknowledging and continuing. Notes/quantities
are displayed read-only when returned; edit those, recipes and store details in
AnyList. The card does not expose delete, rename, reorder or recipe operations.

## Pinned AnyList connector

`anylist-pins.json` records **moryoav/ha-anylist 0.8.0**, its reviewed commit,
release-archive SHA-256 and exact file manifest. This is an unofficial custom
integration using AnyList account access; upstream compatibility and future
authentication are not guaranteed. Selected HA entities do **not** isolate the
underlying account. Make an explicit integration list selection: upstream can
expose all lists when no selection is stored. Keep the optional meal-plan
calendar URL feature **off** for this scope.

Upstream normally polls every 60 seconds; another device's changes may take a
poll to arrive. Refresh does not prove physical phone synchronization. AnyList
may uncross an existing completed item when it is added again. Its raw item
signature attribute also contains list text; HA state/history/diagnostics must
be treated as private, not redacted merely because a hash attribute exists.

Prepare the public package into a new private directory, from the repo root:

```sh
python3 services/home-assistant-family-hub/prepare_anylist.py \
  --output /PRIVATE/NEW-ANYLIST-PACKAGE
# Or verify a public archive already downloaded:
python3 services/home-assistant-family-hub/prepare_anylist.py \
  --archive /PRIVATE/anylist.zip --output /PRIVATE/ANOTHER-NEW-PACKAGE
```

The preparer checks exact archive/member digests, member paths, duplicates,
links, size limits and Python syntax. Output is private; this does not install
into HA, contact an AnyList account or authenticate it. Installation/config-flow
authentication, restart requirements and live verification belong to the
separately authorized operational deployment. Enter credentials only through
HA's native flow, never source, bindings, command arguments or exported logs.
Review upgrades against the pins before changing the installed package.

## Checks and recovery

The unit checks use synthetic data and need no npm dependency installation:

```sh
python3 -m unittest discover -s services/home-assistant-family-hub -p 'test_*.py'
node --test services/home-assistant-family-hub/tests/*.test.js
node --check services/home-assistant-family-hub/family-hub-card.js
```

For the synthetic browser fixture, serve only this directory on loopback:

```sh
python3 -m http.server 8938 --bind 127.0.0.1 \
  --directory services/home-assistant-family-hub
```

Run `tests/browser-check.js` through the existing local Playwright runner with a
real `page` supplied; it is an async page function, not a standalone Node test.
The harness blocks external requests and uses mocked HA. It checks responsive
layouts, literal hostile-looking text, focus/draft retention, finite recurrence,
DST, uncertain saves and list mutations. Stop the temporary server afterward.
These checks do not prove live AnyList, a production HA resource or real devices.

Before production changes, preserve the native HA calendar/configuration,
custom component, resources and latest dashboard baselines in protected backup.
Keep AnyList's account-side data separate from HA backup/restore claims. Narrow
recovery compares current state with the private after-version, restores only
owned dashboard/resource changes and connector files when safe, and preserves
later calendar/list edits. Removing a dashboard does not delete backend data;
restoring an old HA calendar can overwrite newer dates and is a separate action.
For the agenda-only upgrade, compare the current Hub with the accepted candidate,
restore only the owned Home card/button and inserted Calendar agenda section,
and preserve later edits. Removing Calendar Card Pro must not remove the native
calendar, calendar database, quick-add/shopping module or AnyList configuration.

Upstream reference: [pinned AnyList source and limitations](https://github.com/moryoav/ha-anylist/tree/80aec613d47a5e8873ae7e2322d357c1a912667a).
