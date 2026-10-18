# Reviewed Home Assistant community components

`components.lock.json` records the three owner-authorized components qualified
on Home Assistant Core 2026.10.2 with HACS 2.0.5 on 2026-10-18. It pins upstream
stable tags, resolved commits and release artifact hashes. It is a review record,
not an automatic updater. It does not describe every installed HACS repository.

- **Spook 5.11.1** supplies repair diagnostics. The native integration is loaded;
  Household Health uses its actual registered active, ignored and total sensors.
- **Auto-Entities 1.16.1** builds the read-only attention lists in
  [Household Health](../home-assistant-household-health/README.md).
- **Calendar Card Pro 4.2.0** provides the seven-day and fourteen-day agendas in
  [Family Hub](../home-assistant-family-hub/README.md).

These components run within the existing HA process/frontend. They add no
listener, port, standalone container, volume, provider credential or cloud AI
route. Their files and native integration/dashboard/resource registrations are
part of the existing private HA backup and export scope. Restoring these public
definitions alone does not restore household data or private bindings.

## Installation and qualification

Use the installed HACS native repository info/download flow with an explicit
version. Verify the native installed version and the installed bytes against
the locked upstream artifacts. For Spook, compare every archive member with
the installed integration, excluding generated Python caches. Frontend modules
and Calendar Card Pro's editor must match their separate reviewed artifacts.

HACS manages both module resources. Preserve existing resources and refuse
duplicates. Do not replace HACS storage, copy credentials or enable automatic
upgrades. Before a needed Core restart, pass its native configuration check and
record current media state through read-only observation. Configure Spook via
its native empty user flow after restart, then verify the entry is loaded and
the actual repair sensors report valid counts. Do not invoke its repair,
ignore-all, restart, reload, cleanup or cloud-control actions during qualification.

Before saving a dashboard, retain a private checkpoint and compare it with a
fresh native read. Apply only the reviewed scoped transformation. Verify exact
save/readback and authenticated rendering; HTTP success alone does not establish
that a custom card works. Keep raw household state and screenshots outside Git
and RAG. Live voice and media testing remains held unless separately released.

## Recovery and maintenance

Use the dashboard-specific recovery procedures first if presentation regresses.
Restore only the changed card/section or owned resource after checking for later
edits; never overwrite unrelated dashboards with an old full snapshot. Removing
a dashboard does not require deleting calendar, list or device data. Uninstall a
component only after checking its consumers, through its native HA/HACS flow;
keep earlier resources/integrations intact. A backend uninstall may require a
separately checked Core restart.

Qualify later releases before changing this lock: compatibility, installed and
served bytes, module/editor loading, actual list/agenda rendering, responsive
layout, read-only controls and preservation of the owned forms and data. A
passing source check does not establish physical tablet or spoken acceptance.

Primary sources: [Spook](https://github.com/frenck/spook),
[Auto-Entities](https://github.com/thomasloven/lovelace-auto-entities),
[Calendar Card Pro](https://github.com/alexpfau/calendar-card-pro).
