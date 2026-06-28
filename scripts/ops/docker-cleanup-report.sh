#!/usr/bin/env bash
set -euo pipefail

echo "# Docker Cleanup Report"
echo
printf "Generated: \`%s\`\n\n" "$(date --iso-8601=seconds)"
echo "This is report-only. It does not prune images, containers, volumes, or build cache."
echo

echo "## Docker system df"
docker system df || true
echo

echo "## Reclaimable details"
docker system df -v || true
echo

echo "## Dangling images"
docker images --filter dangling=true --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}' || true
echo

echo "## Exited containers"
docker ps -a --filter status=exited --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' || true
echo

echo "## Unused local volumes (candidate list only)"
comm -23 \
  <(docker volume ls -q | sort) \
  <(docker ps -aq | xargs -r docker inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{println}}{{end}}{{end}}' | sort -u) || true
