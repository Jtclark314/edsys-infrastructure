# Work laptop Office and Remote workspaces

Install Microsoft PowerToys per user from its verified official release. Enable
Workspaces and FancyZones, then run `Install-EdSysWorkspaces.ps1` in the normal
Windows desktop session. Keep `Start-EdSysWorkspace.ps1` beside the installer.
The installer requires the verified three-monitor, 1920x1080 / 100% primary
layout, discovers current monitor and Codex package identifiers, backs up
existing profiles, and preserves unrelated workspaces.

Desktop shortcuts:

- **EdSys - Office**: Codex and project Explorer on the primary screen;
  Bluebeam on the middle screen; Outlook and EdSys Portal on the right screen.
- **EdSys - Remote**: Codex and EdSys Portal side by side on the primary 1080p
  screen; Bluebeam, Outlook, and project Explorer available minimized on that
  same screen. Use the taskbar or Alt+Tab to bring them forward.

The launchers reuse existing application windows, explicitly open `R:\` when
needed, then correct placement after the native PowerToys launcher completes.
This handles Explorer's desktop-shell match and applications that restore their
own maximized position during startup. No display is disabled, no resolution is
changed, and existing documents are not closed. All matching app windows are
moved into the selected layout. Status reports are in `%LOCALAPPDATA%\EdSys`.

Backups are `%LOCALAPPDATA%\EdSys\Workspace-Backup-*`; restore their original
`workspaces.json` while no workspace launch/editor is running to undo a profile
change. Remove only the two EdSys shortcuts and `EdSys\Workspaces` scripts to
remove these launchers. PowerToys is independently removable from Windows Apps.

Schema and launcher behavior were checked against Microsoft's PowerToys
v0.101.2362.0 source and the installed Windows runtime. Future PowerToys schema
changes require revalidation. Do not store application titles, document paths,
or raw workspace snapshots in Git or RAG.
