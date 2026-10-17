# Home Assistant Family Hub

Source for a responsive family calendar and selected shopping lists, using the
existing authenticated HA frontend. Operational deployment/authentication status
is separate: **AnyList authentication and live round-trip verification are
pending at this checkpoint.** Source or synthetic browser checks do not establish
production UI, physical phone or tablet acceptance.

## Components and boundaries

- `build.py` creates the new `edsys-family` dashboard with Home, Calendar,
  Shopping and Help views. `add_navigation` adds one navigation-only button to
  an explicitly selected existing Sections view without replacing its cards.
- `family-hub-card.js` registers `custom:edsys-family-event-card` and
  `custom:edsys-family-shopping-card`. Cards use HA's supplied `hass` session,
  native WebSocket/service APIs and calendar readback; there is no separate
  login, token field, server, listener or cloud AI integration.
- Cards add no browser-storage persistence. Drafts, list responses and uncertain
  write locks live only in each card instance's RAM. Ordinary `hass` updates
  preserve drafts/focus; navigation, reload or card recreation can discard them.
  After an uncertain save, inspect the backend before re-entering the draft.

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
item UID, with displayed checkbox state coming from backend readback.

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

Upstream reference: [pinned AnyList source and limitations](https://github.com/moryoav/ha-anylist/tree/80aec613d47a5e8873ae7e2322d357c1a912667a).
