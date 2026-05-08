# AI Context - EdSys Infrastructure

## Repo Purpose

This repository holds deployable EdSys infrastructure definitions. It is for Docker Compose stacks, deployment scripts, config templates, service definitions, and implementation-focused documentation.

Use this repo when the task is about how a service is deployed or maintained.

## What Belongs In This Repo

- Docker Compose stacks and service directories.
- Deployment scripts.
- Safe config templates.
- `.env.example` files with placeholders.
- Service definition YAML files.
- Implementation docs and operational commands.

## What Does Not Belong In This Repo

- Live `.env` files.
- Passwords, tokens, API keys, tunnel credentials, private SSH keys, or VPN credentials.
- Runtime databases, uploads, logs, backups, Docker volumes, or cache directories.
- Raw unsanitized live config snapshots.
- General EdSys knowledgebase notes that belong in `EdSys-Master`.

## Important Folders

- `docs/`: deployment standards and implementation guidance.
- `docker/`: Docker Compose stacks and service-level deployment folders.
- `config/`: sanitized config templates and examples.
- `scripts/`: reusable deployment and admin scripts.
- `templates/`: starter files for new services.

## Relationship To The Other Repos

- `EdSys-Master`: documents what exists and why.
- `edsys-infrastructure`: defines how services are deployed.
- `edsys-infra-configs`: stores sanitized known-good snapshots and audits for comparison and recovery.

## Rules For AI Assistants

1. Inspect `git status` before editing.
2. Do not include real secrets or runtime data.
3. Prefer service folders with clear README files.
4. Use `.env.example` and placeholders for all sensitive or site-specific values.
5. Keep templates reusable and conservative.
6. If adding a stack, document ports, volumes, backup needs, and recovery notes.
7. Coordinate high-level architecture changes back to `EdSys-Master`.

## Secret-Handling Policy

Never commit real secrets. Do not infer or invent values for tokens, tunnel IDs, API keys, passwords, private keys, or VPN credentials. If a command requires a secret, document the variable name and where the user should provide it locally.

## How Future Codex Work Should Proceed

1. Read `AI_CONTEXT.md` and relevant files under `docs/`.
2. Check for existing service patterns before creating new ones.
3. Create or update templates with placeholders only.
4. Avoid touching unrelated local files or untracked experiments.
5. Run syntax checks where practical.
6. Run a risky-string scan before commit.
