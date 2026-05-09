# EdSys Infrastructure Docs

Status: starting baseline.

This folder documents deployment standards for EdSys infrastructure. It should stay implementation-focused and should point high-level source-of-truth notes back to `EdSys-Master`.

## Index

- `DEPLOYMENT_MODEL.md`
- `SERVICE_DEFINITION_STANDARD.md`
- `DOCKER_COMPOSE_STANDARD.md`
- `CONFIG_TEMPLATE_STANDARD.md`
- `../docker/edsys-control-api/README.md` - read-only API and dashboard over the EdSys-Master source-of-truth YAML.
- `../scripts/backup/README.md` - Google Drive offsite backup tooling using `9950x`, `restic`, and `rclone`.

## Rule

Use this repo for deployable definitions and repeatable commands. Do not store runtime data or secrets here.
