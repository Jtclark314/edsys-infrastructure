# Service Definition Standard

Status: starting baseline.

Use service definition YAML files to describe deployable services in a structured way. These files are implementation references, not secret stores.

## Required Fields

- `name`
- `description`
- `host`
- `runtime`
- `ports`
- `volumes`
- `environment`
- `secrets_required`
- `backup`
- `monitoring`
- `restore_notes`
- `owner`
- `last_verified`

## Guidance

- Use placeholders for sensitive values.
- Mark uncertain values as `to be confirmed`.
- Include enough information for an AI assistant to find the right stack and understand risk.
- Keep high-level service ownership and architecture in `EdSys-Master`.
