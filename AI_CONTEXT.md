# AI Context — EdSys Infrastructure

This repository owns deployable EdSys definitions: service directories, Docker
Compose, scripts, safe templates, and implementation documentation. It is not a
live runtime export or the general knowledgebase.

Read this file first. `AGENTS.md` owns repository working rules. Use
`docs/README.md` to select the applicable standard and the owning service README;
read only the procedures required by the task.

Keep code and deployment detail here. Coordinate architecture and operating
policy changes with `/home/jeremy/code/EdSys-Master`. Source files describe
intended behavior; verify current runtime separately before consequential work.

Credentials, real `.env` files, databases, logs, uploads, backups, Docker volumes,
caches, and raw machine state remain outside Git. Use placeholders and
`.env.example` for safe templates. Preserve unrelated edits and distinguish
plan, implementation, and deployment authorization.
