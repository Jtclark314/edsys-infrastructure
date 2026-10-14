# Home Assistant conversational voice

Status, 2026-10-13: general chat, web search and read-only EdSys lookup are active
in the existing named voice profile. Candidate and production pipeline/audio,
backend-failure and synthetic full-speech checks passed. The owner confirmed
audible native time, chat and knowledge replies through Alt+V on Nimo.
Web audibility, Wi-Fi handover and physical Pi commissioning remain pending.
Qualified native Core version: **2026.10.1**. See the current EdSys-Master voice
record for the final deployment/acceptance checkpoint.

[Owning architecture](https://github.com/Jtclark314/EdSys-Master/blob/main/docs/HA_CONVERSATIONAL_VOICE.md).

## Behavior and authority

Nimo's existing Alt+V and the prepared Pi's voice endpoint use the named
`EdSys Living Room Voice` pipeline. Existing HA Cloud STT/TTS and AmberNeural
are retained. Native HA intents run first; general conversation uses native
OpenAI Conversation with `gpt-6.1-sol`, low reasoning, low verbosity, Standard
service, no Pro mode and a 2048-token response ceiling. Replies usually use
one or two sentences and at most 60 words. Web search is enabled for changing
public facts. This remains dependent on existing Internet/Cloud services.

`profile.json` and `prompt.jinja` are the sanitized settings. Request storage,
home-location disclosure and Code Interpreter are disabled. This is not a
claim of provider zero-retention. The owner created/entered the key directly
in HA's native OpenAI integration. The key stays in protected native HA state;
no provider credential belongs on Nimo, Pi, the hub, Git or RAG. The integration's
default AI Task/STT/TTS subentries are retained but unused by this voice path.

The selected OpenAI backend receives **only** the read-only EdSys knowledge API
plus built-in web search. It has no Assist API, shell, device-write, messaging,
file-write or scheduling tool. Normal existing HA entity exposures and household
profile preferences remain authoritative. Unsupported home requests are not
converted into invented actions. The retired Phil/Gateway stacks stay retired;
ChatGPT Dot memory/apps are not inherited by this API assistant.

## Why the adapter exists

Core 2026.10.1 marks any OpenAI tool/API attachment as CONTROL, including a
read-only documentation reader; this changes native fallback intent filtering.
The small `EdSys Voice` conversation entity advertises zero device-control
features and delegates through HA's public conversation API to the native
OpenAI backend. This preserves native handling while attaching read tools.
No Core or OpenAI integration source is patched. The adapter stays available
when the backend is disabled/unavailable so native commands can continue;
general-chat delegation has a 45-second timeout. Cancellation is not swallowed.
HA Cloud STT/TTS still require their existing connectivity.

## Knowledge scope and freshness

The existing reviewed grounding index on 9950x is exported to a filtered local
SQLite snapshot. Foothills/Procore, financial, Excel, homeowner, meeting and
Kindle records are excluded. Search returns documentation, not live health.
Source audit dates and document locations accompany bounded excerpts; document
locations must not be mistaken for operational service paths.

- Canonical source: `/mnt/ai-store/rag/grounding/edsys-grounding.sqlite`
- HA private catalog: `/config/edsys_voice_knowledge/index.sqlite`
- Query: at most 512 characters; four results, at most two per source
- Excerpts: at most 1200 characters each
- Freshness: actual canonical index build age at most 30 minutes
- Missing, corrupt, symlink, wrong-schema/scope and future/stale catalogs fail closed
- SQLite reads use read-only mode and `query_only`; queries are parameterized

The refresh runs every five minutes as the existing 9950x operator user. It uses
the existing privileged SSH/QEMU-guest route with a fixed VM/container/destination,
atomic replacement, bounded decompression, integrity and SHA-256 readback checks.
No listener, new network credential or immediate hub call is added to a voice
turn. If the hub stops refreshing, knowledge fails closed after its freshness
window; general chat and native home intents remain separate.

## Installation and configuration

Before change, preserve the existing pipeline and a recovery checkpoint. On
2026-10-13 `pre-openai-voice-20261013` completed after the owner entered the key.
Native OpenAI onboarding must use the ordinary owner's secure HA interface.

1. Dry-run `configure.py --private-state PRIVATE_DIR`, then use `--apply` only
   under existing owner authorization. It creates an isolated native candidate
   and retains speech settings and the working/preferred profiles. Native form
   field checks reject unsupported inputs before submission. Inspect actual
   state after failures; creation requests are never automatically replayed.
2. Run `sync_knowledge.py` and `install_knowledge.py`. They write only this
   catalog/component. Validate Core configuration and native imports. A normal
   Core restart may be required to register a newly installed custom domain.
3. Add `EdSys Voice Knowledge` through native config flow, selecting the new
   OpenAI backend. Attach only `edsys_knowledge` to that backend. Point the
   candidate at the adapter, retaining `prefer_local_intents: true`.
4. Verify the candidate, including actual search/tool use, matching sources,
   no-source/stale refusal, reply audio, native routing and backend unavailability.
   Then switch only the existing named target pipeline's conversation engine.
5. Install the two user units in `systemd/`, reload and enable/start the timer.
   Validate a real user-unit refresh, not only a direct shell run.

Use `verify.py` with private state for read-only native time, general chat,
actual web search and reply-audio checks; `--speech-fixture` also traverses
STT and `--knowledge` verifies a real knowledge-tool invocation. These synthetic
fixtures do not establish room acoustics, human audibility or Wi-Fi handover.
Use `--production` for the saved target profile and `--case` for a single
time/general/web/knowledge/unknown-host/backend-unavailable fixture. The latter
requires deliberately disabling only the candidate backend, inspecting actual
state, then restoring and verifying it; never repeat an uncertain mutation.
Python/unittest and native runtime checks must pass after changes; verify the
public `supported_features` property, not Core's transformed private class field.

## Recovery and acceptance

Prefer restoring only the target pipeline's saved native conversation engine
to a full VM rollback. Keep the household preferred pipeline untouched. Stop
the refresh timer and unload/remove only this knowledge entry/component when
withdrawing the feature. Retain the owner's OpenAI integration and key unless
explicitly asked to remove them. Private state, catalogs, fixtures, snapshots,
auth and browser profiles stay outside Git/RAG.

Model/provider limits and budgets remain owner-controlled. Do not claim a hard
monthly spend cap or inspected key permissions. Requalify after HA/model changes.
Physical Pi commissioning and room/soak tests remain separate. Additional Pi
services remain deferred until physical voice acceptance.
