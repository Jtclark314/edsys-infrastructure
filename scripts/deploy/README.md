# Deployment Helpers

Status: implementation helpers for reviewed EdSys deployments.

Scripts in this folder may prepare local directories, templates, or ignored runtime files needed by a deployable stack. They must not print private values or add runtime state to Git.

## Scripts

- `prepare-9950x-workhorse.sh` - prepares `/opt/edsys-workhorse`, generates the private ignored workhorse environment file when missing, writes private SearXNG settings, and links the stack-local `.env` symlink to the private file.

Run from the infrastructure repo:

```bash
cd /srv/edsys/edsys-infrastructure
scripts/deploy/prepare-9950x-workhorse.sh
docker compose -f docker/9950x-workhorse/compose.yaml config --quiet
```
