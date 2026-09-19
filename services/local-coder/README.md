# Local Qwen coding agent

Owner: Jeremy. Host: 9950x. Runtime state and qualification evidence are under
`/mnt/ai-store/local-coder/`, outside Git. High-level acceptance belongs in
`EdSys-Master/docs/LOCAL_CODING_AGENT.md`.

## Components

- Official Qwen3.6-35B-A3B Q8_0 weights, pinned by manifest and full weight hash
  in `artifacts.json`. Ollama calls the architecture 36.0B including its other
  components; this is the requested 35B-A3B family, not another model.
- Ollama 0.33.2 at the existing reviewed image digest; a separate
  `ollama-coder` service publishes only `127.0.0.1:11435`. Its internal broker
  address is `http://ollama-coder:11434` on the existing workhorse network.
- CPU-only inference: 12 CPU quota/threads, 48 GiB memory ceiling, no swap,
  one active request/model, four queued requests, and two-minute idle unload.
  No GPU device is admitted. Root filesystem and shared model store are
  read-only, capabilities are dropped, and privilege escalation is disabled.
- `edsys-qwen36-coder:latest` is the local configured alias: 262,144 context,
  16,384 maximum generated tokens, Qwen's precise-coding sampling settings,
  and preserved upstream vision/tool/thinking behavior. OpenCode permits two-hour
  inference waits and the identity bridge allows a 125-minute idle upstream wait
  for large CPU-only prompts. Here `latest` names
  the local alias; weights and engine are explicitly pinned.
- OpenCode 1.18.31 and Playwright MCP 0.0.81 are locked in the npm manifest.
  Python MCP 2.2.0 and Pillow 12.3.0 plus dependencies use `requirements.lock`.
- OpenCode model, auxiliary model, and subagents all use the local provider.
  Reasoning content is preserved across calls. Sharing and automatic upgrades
  are disabled; the launcher uses private XDG state and disables project config
  overrides, unreviewed plugins, remote model-catalog fetches, and inherited OTEL
  exporters. One reviewed local context hook is enabled; unexpected global/home
  config or plugin surfaces stop the launcher. Project config remains disabled.

## Use

```bash
cd /path/to/your/project
edsys-code
edsys-code --tools code
edsys-code --tools browser
edsys-code --tools desktop
edsys-code --tools code run 'Inspect this bug and suggest a fix'
```

The default `all` profile enables all seven MCP connections. EdSys, Code Intelligence, Context7 and Microsoft Learn remain enabled in every tool profile; GitHub is enabled in `all` and `code`. Narrow profiles save context when
the task only needs code, browser, or desktop tools. Code edits and delegation are enabled. Jeremy explicitly authorized full terminal
access on 2026-09-18: Bash and external project paths are allowed without routine
approval clicks. On 2026-09-19 Jeremy also authorized automatic browser/desktop,
web, file-read and repeated-tool execution; fixed agent iteration caps were removed. The web worker
allows the existing account sudo policy to apply (`NoNewPrivileges=false`); no
new sudo rule, account, key, or management listener is installed. These are
workflow controls, not an OS sandbox or permission for unrelated actions.

Playwright uses a separate ephemeral, headless Chrome profile with its browser
sandbox enabled. The desktop MCP starts a separate Xvfb display at `:90` or
higher, protected by Xauthority, with a private D-Bus session and Xfwm4 window
manager. Screenshots are 1280x800; click/drag coordinates use Qwen's normalized
0–1000 scale on each axis and are converted to bounded screen pixels. It supports click/double-click, type, keys,
scroll, drag, and launching Chrome, Mousepad, a calculator, or the scratchpad.
The operator's physical `:0` and XRDP `:10` sessions are not targeted. These
processes run as Jeremy: separate desktop/browser sessions do not provide
filesystem or network isolation. Screenshots and working notes stay private.

The existing LiteLLM broker also exposes `edsys-coder-agent-local` with a model-specific
7200-second timeout for large CPU-only prompts. Its dedicated
service-scoped credential is stored outside Git under
`/opt/edsys-workhorse/litellm/service-keys/edsys-local-coder.env` with root-only
permissions. Existing client allowlists and model aliases are preserved. The
direct local launcher requires no broker credential. Broker credentials can be
reissued through the existing service-key workflow if restoring from source.

## EdSys context and project continuity

The launcher requires two absolute instruction files: local `AGENT_RULES.md`
and `EdSys-Master/docs/context-packs/LOCAL_CODER_STARTUP.md`. Together they
provide roughly 3,800 words of operating rules, device aliases/roles, software
relationships, source ownership and working conventions. They are reviewed
source, not live health. The launcher refuses missing files or a combined
36,000-character budget overflow. OpenCode loads them for primary and delegated
sessions and reloads instructions on subsequent model turns, including after
compaction. This is external context, not model training.

Pinned OpenCode's `OPENCODE_DISABLE_PROJECT_CONFIG=1` also suppresses automatic
root AGENTS loading. Keep provider isolation. The reviewed `context-plugin.mjs` automatically supplies
the current worktree's root AGENTS (CLAUDE fallback), latest private checkpoint
and grounding freshness on every model call and before compaction. The
`edsys_session_context` tool provides an explicit refresh when needed. Nested
instructions must be read when entering their directories. The project is the
MCP process cwd resolved to its Git worktree root, not the web service's initial
working directory. The hook invokes the read-only Python context reader with
argument arrays, uses only Node built-ins and makes no network request. Context
reads write nothing; an idle-event hook refreshes the separate private archive. Missing/corrupt context stops the model call instead of silently dropping
notes. The launcher admits only this absolute local plugin, rejects other
auto-discovery surfaces, clears inherited config overrides and disallows `--pure`
(which would suppress the required hook). No global project-memory file is copied
across projects.

`knowledge_mcp.py` exposes six tools through the existing pinned Python MCP
runtime, with no new package, cloud request, listener or duplicate records DB:

- `edsys_session_context`: project guidance/checkpoint and index health.
- `edsys_search_records`: bounded, source-cited FTS over the existing grounding
  SQLite file; historical/superseded records excluded by default.
- `edsys_read_record`: bounded pages by stable record ID, not arbitrary paths.
- `edsys_search_history` and `edsys_read_history`: bounded private conversation and checkpoint archive retrieval, project-scoped by default.
- `edsys_save_checkpoint`: primary-agent private project notes using an expected
  revision and existing relative evidence files. General/explore agents are
  denied this tool and return findings to their parent.

Each records query opens the grounding index read-only, accepts schema 1, enforces its existing
15-minute freshness budget, verifies retrieved files against indexed hashes,
and checks reviewed source roots. Results include path, hash, status, available
audit date, modification date and index time. An index rebuild is not a fresh
source audit. Historical retrieval requires an explicit tool flag. No result,
stale index or changed source is an explicit gap, not a fabricated answer.
The adapter does not reproduce the Portal's deterministic answer-citation gate;
model use of the evidence and citations still needs review.

Private checkpoints live at
`/mnt/ai-store/local-coder/project-memory/<sha256-of-worktree-path>/checkpoint.json`.
They contain outcome, completed work, decisions, next steps, blockers and file
hashes. Locking, compare-and-swap revisions, atomic writes and all prior revisions
protect concurrent sessions. Files are 0600 and project directories 0700.
Changed/deleted evidence is marked on recall. Hashes prove file identity, not
the truth of model-written claims. Obvious credential patterns are rejected;
this is not a comprehensive data-loss prevention system. Never store secrets,
transcripts or business records. Notes are advisory and stay outside Git/RAG;
reviewed shared facts continue through the existing source publication process.

Use `./install-context.sh` to apply these components to an existing installation
without reinstalling weights or restarting inference. It checks dependencies,
runs focused tests, refuses a busy web instance, installs the PowerShell helper
and reloads only the active OpenCode web unit. Let user work finish, or obtain
explicit authorization before cancelling a stalled session. Run `install-web.sh`
for a fresh web installation. Restore previous source/config and unit from Git
for rollback; retain private checkpoints and sessions. Private checkpoints and conversations now use the encrypted backup chain described below.

## Terminal and PowerShell

The native Bash tool runs on 9950x and can invoke existing local programs and
SSH aliases. `edsys-powershell --list` describes supported routes. Examples:

```bash
edsys-powershell --host 9950x --file ./task.ps1
edsys-powershell --host nimo --file ./task.ps1
edsys-powershell --host basecamp --file ./task.ps1
ssh -o BatchMode=yes pve-node3 hostname
```

Local files run with `pwsh -File`. Remote scripts travel over SSH stdin to a
small encoded bootstrap using strict host-key checks and existing accounts;
quotes/newlines/Unicode survive, and no execution-policy bypass is added.
Remote code is a scriptblock: it has no transferred file or remote PSScriptRoot.
For scripts with sibling-file dependencies, use the normal terminal/SFTP workflow
and run the actual remote file. The maintained Windows routes use PowerShell as the SSH default shell; the
helper explicitly propagates the nested interpreter exit code through it.
Nonzero exit codes propagate; the default client
timeout is 300 seconds and is configurable. A timeout cannot guarantee every
remote child exited, so inspect before retrying consequential commands.

PowerShell file write/read/cleanup acceptance passed on 9950x (PowerShell 7.6.6),
Nimo and Basecamp (Windows PowerShell 5.1). Existing SSH read-only hostname checks
passed on router, pve-node0–3, primary/secondary Pi-hole, arr-vm, ingress,
node1-services, family-services, NetBox and living-room Pi5. These Linux hosts
have native shells; those checks found no remote `pwsh`. Windows-specific
cmdlets still require a Windows host.

The work laptop was online but rejected TCP 22; no inbound execution route is
configured. Maktop's server/SSH role was retired. Neither is claimed remotely
executable. Adding those endpoints requires a device-side authenticated
management setup and its own verification. Home Assistant/appliances and
isolated lab guests retain their owning management procedure; “full terminal”
does not create an arbitrary shell or administrator account on every device.

## Private web interface

The persistent OpenCode UI is `https://9950x.taile832fe.ts.net:8444/`, available
through Tailscale, including Nimo. Nimo has a **Local AI - OpenCode** desktop
shortcut to the prepared **Local AI Lab** session; the private session link is
stored outside Git. If starting from the home page, use **Add project** to select
`/home/jeremy/projects/local-ai-lab`, then **New session**. Qwen is preselected;
browser and desktop MCPs use the same qualified local profile. Computation and
files stay on 9950x. The backend starts in this fresh project directory.

`install-web.sh` installs/enables two lingering user services:
`edsys-local-coder-web.service` serves OpenCode on loopback 4096;
`edsys-local-coder-web-proxy.service` runs the Node identity bridge on loopback
4097. Tailscale Serve owns HTTPS 8444. Existing 443/8443 routes are preserved;
Funnel is not enabled. The bridge admits only the configured owner identity
injected by Serve, checks the public host and HTTPS route, and rejects foreign
origins. Mutations and WebSocket upgrades require the same origin. Streaming
responses and WebSocket connections pass through without buffering.

OpenCode also requires an internal Basic credential injected by the bridge.
The browser never receives this credential. Owner configuration and generated
credentials live in `/mnt/ai-store/local-coder/web/` with private permissions,
not in Git. Missing identity, a wrong owner, or direct unauthenticated backend
access fails closed. Other processes running as Jeremy remain within the same
OS trust boundary. Neither service grants sudo access.

Verify with `systemctl --user status edsys-local-coder-web{,-proxy}.service`,
the HTTPS `/global/health` and `/mcp` endpoints, and
`node --test tests/test_web_proxy.mjs`. Nimo HTTPS/browser loading and a controlled
service restart passed. Full-host reboot acceptance remains to be confirmed.

To restore, run `install-web.sh` after restoring the pinned client/runtime. It
retains existing private credentials and requires the same owner identity.
User lingering is already enabled on 9950x. To roll back only web access, run
`tailscale serve --https=8444 off`, then disable/stop the two user services.
Do not reset all Serve configuration. CLI/model use remains independent.
`install-nimo-web.ps1 -Url <private-session-url>` restores the shortcut without
overwriting an unrelated file. If the initial session is deleted, use the base
UI URL to start another; session links are not credentials or durable source.

## Install and reproduce

Run `./install.sh` only for an authorized install or recovery. It requires
mounted AI Store, the pinned Ollama image, the existing shared Ollama service,
the existing broker network, and the listed host GUI tools. The installer pulls
the named source model and fails closed if its official manifest has drifted,
verifies the complete weight checksum, creates the alias, installs locked
clients, and starts only this Compose service. It does not upgrade shared
Ollama, change Codex, or select a cloud provider.
It then runs `install-expansion.sh` to install the pinned GitHub connection,
verify context tools and wire the private archive and existing backup service.

The shared Ollama store holds one copy of the 38.7 GB weights. The dedicated
server mounts it read-only. Do not remove the source tag, alias, or referenced
blob during unrelated model cleanup. A model upgrade requires a deliberate
artifact-lock review and renewed tool/vision/coding qualification.

## Verify and recover

```bash
python3 verify_model.py --full
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml ps
curl --fail http://127.0.0.1:11435/api/version
docker exec ollama-coder ollama show edsys-qwen36-coder
docker exec ollama-coder ollama ps
edsys-code --version
edsys-code mcp list
/mnt/ai-store/apps/local-coder/desktop-venv/bin/python -m unittest discover -s tests -v
```

The nonblocking applications tier in the ordered recovery manifest includes
this service and its loopback API gate. Docker's existing storage mount guard
and `unless-stopped` policy apply. Full-host reboot acceptance is separate from
the controlled service restart check; do not reboot production just to test it.

If inference fails, inspect `docker logs --tail 80 ollama-coder`, host available
memory, and the private OpenCode log under `data/opencode/log/`. A healthy API
alone does not prove useful model output. CPU prompt processing and generation
are bounded by hardware; changing the 262,144-token context or concurrency requires fresh
memory and agent tests.

## Rollback and backup

Stop only this service with `docker compose -f compose.yaml stop`; preserve
weights and private session data for diagnosis. Remove its recovery-manifest
entry if intentionally disabling the service. The existing shared model routes
remain the previous working baseline. Restore the launcher/config from Git and
use the artifact/dependency locks for rebuilds.

Weights, npm/Python environments, and browser caches are replaceable downloads;
they do not require backup as unique source. Source/configuration are protected
by Git. Conversations, screenshots, and desktop notes are private
local working state outside Git/Obsidian/RAG; the staged encrypted backup and
direct Google Drive restore were verified on 2026-09-19. Do not copy raw qualification logs into documentation.

## Upstream references

- [Qwen model and sampling guidance](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- [Official Q8 artifact](https://ollama.com/library/qwen3.6:35b-a3b-q8_0)
- [Ollama tool loops](https://docs.ollama.com/capabilities/tool-calling)
- [OpenCode provider configuration](https://opencode.ai/docs/providers/)
- [Playwright MCP](https://github.com/microsoft/playwright-mcp)

## Context acceptance notes

Twenty Python unit tests and two hook tests passed. A local protocol fixture
captured actual OpenCode parent, child and continuation requests and proved that
each received both project guidance and private checkpoint content even with
all MCP tools disabled. This protocol test uses a fake provider; it is evidence
of client injection, not Qwen reasoning quality.

Real Qwen fixed the disposable function, passed 3 project tests and 11 independent
edge cases, delegated an EdSys ownership lookup, ran all three PowerShell targets
and saved a checkpoint in one turn. A fresh web session recalled the checkpoint,
reported unchanged evidence, identified the project marker and refused to invent
an unknown device. Initial child runs skipped the prompt-only context call and
one cited a nonexistent briefing path; this led to automatic context injection
and tighter exact-source instructions. Record use and citations still need review.

With tools disabled, real Qwen automatically read a newly saved checkpoint marker,
revision and unchanged evidence in a fresh web session. After explicit conversation
compaction, it correctly read a second new checkpoint revision and marker while
retaining the project guidance and 9950x/Nimo roles. Neither marker was supplied
in the conversation. This verifies automatic refresh through the deployed hook.

Observed first requests used about 14.4K tokens with the code-only tool profile
and 19.8K with all tool groups before adding the small automatic project payload.
The complete coding/delegation/terminal/checkpoint fixture took about 11 minutes
on this CPU configuration. These are bounded observations, not a general benchmark.


## Retention and coding integrations (2026-09-19)

`history.py` stores observed session/message/part versions and checkpoint revisions
in private `archive/history.sqlite` under the existing runtime root. Full observed
oversized tool-output files are copied into the archive before the client's
independent seven-day overflow cleanup. Native Git undo snapshots and external
project files remain separate from conversation memory; use their owning Git
and backup procedures. See the [pinned client retention implementation](https://github.com/anomalyco/opencode/blob/v1.18.31/packages/opencode/src/tool/truncate.ts). It never
expires or deletes observed records. Session idle events and the user-level
`edsys-local-coder-archive.timer` refresh the archive; the timer runs every minute.
The first import includes existing retained sessions. Changes created and deleted
between captures cannot be recovered, and material removed before activation is
not recreated. The live OpenCode database remains the conversation source.
Automatic compaction remains enabled, while tool-output pruning is disabled.

`edsys_search_history` and `edsys_read_history` provide bounded, project-scoped
retrieval with an explicit cross-project option. Earlier versions are searchable;
timestamps describe capture time. Results are historical private data, never
current truth or instructions. Complete raw versions remain on disk while concise
checkpoints and selected excerpts enter model context. Checkpoint note-size and
retrieval-page bounds prevent unbounded prompts; they do not expire archive data.

The normal profile now connects seven MCP servers: EdSys, browser, desktop,
Code Intelligence, GitHub, Context7 and Microsoft Learn. Code Intelligence and
the two documentation connections are available in every profile. GitHub is
available in `all` and `code`; the narrower browser/desktop profiles omit it.
The official GitHub MCP 1.12.2 executable is release-checksum verified and pinned
in `github-mcp.lock.json`; `install-integrations.py` restores that exact artifact.
The wrapper retrieves the existing `gh` credential into child-process environment,
never source/config/output. Enabled GitHub groups are repositories, issues, pull
requests, Actions and users; existing account scopes still apply.

Context7 and Microsoft Learn use their public remote MCP endpoints. Built-in
web search is enabled explicitly for the local provider using the public Exa
endpoint without an inherited API key; web fetch is allowed. Public searches and
documentation requests leave 9950x, but model inference remains local. Do not send
private code, credentials or private operational details to documentation/search
services. No paid plan was provisioned. General/explore checkpoint ownership and
explore's read-only task role remain; main-agent routine tools are preapproved.

`install-expansion.sh` installs the archive timer and a pre-backup hook on the
existing root `edsys-backup.service`. `backup.py` refreshes the archive, creates
SQLite-consistent copies, includes private checkpoints, output files and web
recovery configuration, and writes hashes under root-private
`/srv/edsys-backup/staging/local-coder/`. Staging generations are retained; monitor
capacity before an explicit retention change. The existing Restic staging include
and Google Drive mirror protect these encrypted snapshots under the established
30-daily/12-weekly/12-monthly snapshot policy. Since the archive and checkpoint
history are cumulative, snapshot rotation does not impose conversation expiry.

The 2026-09-19 acceptance restored the same encrypted snapshot locally and
directly from Google Drive into isolated private directories. All 21 payload-file
hashes, both SQLite integrity checks and a restored history search passed. Recovery:
restore to isolated staging, run `python3 backup.py --verify <generation>`, stop
the archive timer/service and OpenCode, retain the displaced live state including
each database and its `-wal`/`-shm` siblings together, then restore the verified
DBs to `data/opencode/opencode.db` and `archive/history.sqlite`, and restore the
private folders with Jeremy ownership and 0700/0600 permissions. Treat web state
as credentials; restore only through the existing owner-authenticated route.
Never leave old WAL/SHM files beside restored database files. Do not overlay an
active SQLite database. Resume services and the archive timer, then verify context,
history search, MCP connections and owner access. Host-reboot acceptance remains
separate. No Codex memory or shared RAG corpus receives private archive content.

## Expanded context acceptance (2026-09-19)

The 131,072-token window recalled all three first/middle/last markers from a
76,025-token input in 835 seconds. The 262,144-token window recalled all three
from a 136,825-token input in 2,049 seconds, with zero GPU allocation and about
41.7 GiB observed container memory inside the 48 GiB cap. Actual input counts
confirmed that neither accepted test was truncated to the previous window.
The initial oversized fixture exceeded 128K and triggered normal context shifting;
it was corrected before qualification. This is bounded long-context recall and
allocation evidence, not a full-window coding reliability benchmark.

The accepted default is now 262,144 tokens for OpenCode, the model alias and
the dedicated engine. Generated output remains bounded at 16,384 tokens;
CPU quota, RAM cap and one-request concurrency remain unchanged. OpenCode and
the optional LiteLLM coder route allow two-hour inference waits; the private
identity bridge allows a 125-minute idle upstream wait.

The expansion installer passed 24 Python tests and two context-hook tests;
the two proxy tests also passed. Final web configuration confirmed seven healthy
MCP connections, native web search, preapproved routine tools, no fixed agent
steps and disabled tool-output pruning. Nimo was offline for the final client
recheck; the owner-authenticated HTTPS service was healthy on 9950x.
