# EdSys Infrastructure Agent Guidance

## Start Here

- Read `AI_CONTEXT.md` first.
- Then read `README.md`, `docs/README.md`, and the relevant deployment standard under `docs/`.
- Treat this repository as deployable infrastructure definitions, not live runtime state.

## Rules

- Do not commit secrets, tokens, private keys, tunnel credentials, live `.env` files, runtime databases, uploads, logs, backups, Docker volumes, or raw local machine state.
- Use `.env.example`, templates, and placeholder values only.
- Prefer service folders with clear README files and conservative deployment notes.
- If adding or changing a stack, document ports, volumes, backup needs, restore notes, and verification commands.
- Coordinate high-level architecture or service-catalog changes back to `EdSys-Master`.

## Workflow

- Start every task with `git status --short --branch`.
- Search existing service patterns before creating new folders.
- Keep edits narrow and implementation-focused.
- Run syntax checks where practical.
- Before committing, run a risky-string scan such as:
  `git grep -n -I -E "password|token|api[_-]?key|secret|BEGIN (RSA|OPENSSH|PRIVATE) KEY" -- .`

## Done Means

- Deployable files and templates are sanitized.
- Runtime state and real credentials remain outside Git.
- The relevant service README or deployment doc points to the source of truth.
- Git status is understood and intended changes are ready to review.
