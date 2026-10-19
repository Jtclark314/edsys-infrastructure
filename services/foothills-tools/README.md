# Foothills tool entry points

This directory owns hub transport for existing Foothills capabilities. Application
code and records remain with their current owners. No listener, tunnel, database
copy, credential, or scheduled task is added.

## Observation Tracker

The stdio launcher runs the current deployed release's existing MCP on Basecamp
through its established SSH alias and known host identity:

```text
/usr/bin/python3 /srv/edsys/edsys-infrastructure/services/foothills-tools/observation_mcp_ssh.py
```

It reads the active release pointer on every connection, imports the private
runtime environment on Basecamp, and uses that release's Python environment.
SSH is noninteractive, requires a previously trusted host key, disables agent,
X11 and port forwarding, and makes one connection attempt. The MCP protocol
passes directly over stdin/stdout without transcript logging. Closing the client
closes the connection; no persistent server is installed. Credentials and the
official SQLite database never move to 9950x.

Suggested Codex server name: `foothills-observations`. Use a 30-second startup
timeout and a 120-second tool timeout. Registration belongs in the hub's normal
Codex configuration; this launcher does not edit it.

The deployed application exposes eight tools: `observation_dashboard`,
`search_observations`, `get_observation_record`, `run_observation_report`,
`list_observation_reference_values`, `observation_query_status`,
`ask_observations`, and `observation_query_results`.

This is the application's existing trusted local service actor over authenticated
SSH. It is independent of browser Cloudflare Access authentication and does not
change or bypass the web origin's authentication. MCP has no official-record
write, attachment-download, raw-SQL, or user-management tool. Ask supports local
processing only; it can maintain rebuildable search/audit material, never the
official observation or evidence records. Read all returned scope and coverage
warnings. Production responses must not be placed in Git, GitHub, Obsidian,
EdSys RAG, AnythingLLM, or Ask Foothills.

On 2026-10-18, the actual production process passed initialize, discovery of all
eight tools, query-readiness, and a dashboard database read. Query readiness
reported enabled/available, local configured, read-only and enhanced unavailable.
No official record content or counts were retained in this source record. This
does not establish physical user/browser acceptance or test every query tool.

Failure recovery: check the existing Basecamp SSH route, current release pointer,
release Python/MCP files, and private runtime config. Never fall back to a local
fixture or stale database. Do not weaken Cloudflare Access, copy the database,
or expose stdio through a new network listener. Rollback removes only this
launcher/registration; the deployed application, data and web access remain.

Source and application boundaries:
`/home/jeremy/code/foothills-project-portal/observation-tracker/integrations/README.md`.

## Procore browser

The existing project adapter can also be registered globally under
`foothills-procore-browser` with the same startup/tool timeouts:

```text
/usr/bin/python3 /home/jeremy/projects/foothills/.agents/skills/foothills-procore-browser/scripts/browser_worker.py mcp
```

Use the `foothills-procore-browser` skill. Every retrieval requires exclusive
ownership, visible company/project verification and the applicable ingestion
workflow. On 2026-10-18 its actual adapter passed initialize, discovery of 16
tools, and safe status. Browser reachability and the expected project URL were
confirmed; authentication/identity remains subject to visible verification.
This qualification did not retrieve Procore records or change browser ownership.

## Checks

```text
python3 -m unittest discover -s services/foothills-tools -p 'test_*.py' -v
python3 -m py_compile services/foothills-tools/observation_mcp_ssh.py
```

For live acceptance, use an MCP client to initialize, list tools and call
`observation_query_status` plus a minimal read. Keep raw results transient;
retain only sanitized success/failure evidence. Do not use `--self-test` for
general logging because that command includes production dashboard content.
