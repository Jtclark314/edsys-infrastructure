#!/usr/bin/env bash
set -euo pipefail

echo "# Docker Cleanup Report"
echo
printf "Generated: \`%s\`\n\n" "$(date --iso-8601=seconds)"
echo "This is report-only. It does not prune images, containers, volumes, or build cache."
echo

echo "## Safety policy"
echo
echo "- Do not use an indiscriminate \`docker system prune -a\`."
echo "- Audit stopped-container logs, image age/labels, and rollback value before deletion."
echo "- Treat volumes, stopped containers, and explicitly tagged rollback images as protected by default."
echo

echo "## Docker filesystem and logging defaults"
echo
docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
logging_driver="$(docker info --format '{{.LoggingDriver}}' 2>/dev/null || true)"
printf "Docker root: \`%s\`\n\n" "${docker_root:-unknown}"
printf "Default logging driver: \`%s\`\n\n" "${logging_driver:-unknown}"
if [[ -n "${docker_root}" ]]; then
  df -hT "${docker_root}" || true
fi
echo

echo "## Largest container log files"
echo
if [[ ${EUID} -ne 0 ]]; then
  echo "Log file sizes require root access. Re-run this report with \`sudo\`."
else
  while IFS=$'\t' read -r id name image state log_path; do
    [[ -n "${log_path}" ]] || continue
    size="$(stat -c %s -- "${log_path}" 2>/dev/null || printf '0')"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${size}" "${name}" "${image}" "${state}" "${id}" "${log_path}"
  done < <(
    for id in $(docker ps -aq); do
      docker inspect "${id}" \
        --format '{{.Id}}{{"\t"}}{{.Name}}{{"\t"}}{{.Config.Image}}{{"\t"}}{{.State.Status}}{{"\t"}}{{.LogPath}}'
    done
  ) |
    sort -nr |
    head -20 |
    numfmt --field=1 --to=iec-i --suffix=B ||
    true
fi
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
