#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /tmp/stirling-pdf /tmp/stirling-files /tmp/xdg-1000 \
    /pipeline/watchedFolders /pipeline/finishedFolders /pipeline/defaultWebUIConfigs \
    /customFiles/signatures /customFiles/templates /tmp/office
chmod 700 /tmp/stirling-pdf /tmp/stirling-files /tmp/xdg-1000
ln -sfn /opt/stirling/static /customFiles/static
export XDG_RUNTIME_DIR=/tmp/xdg-1000
export LD_LIBRARY_PATH=/usr/lib/libreoffice/program:/usr/lib/x86_64-linux-gnu
export STIRLING_LO_ALLOW_RW=/dev:/tmp/office:/tmp/stirling-pdf:/tmp/stirling-files:/tmp/xdg-1000:/home/stirlingpdfuser
export STIRLING_LO_ALLOW_SOCK=/tmp

pids=()
cleanup() {
    trap '' TERM INT EXIT
    kill -TERM "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
}
trap cleanup TERM INT EXIT
for port in 2003 2005; do
    profile="/tmp/office/instance_${port}"
    mkdir -p "$profile"
    cp -R /var/lib/libreoffice-template/. "$profile/"
    chmod -R u+rwX "$profile"
    unoserver --interface 127.0.0.1 --port "$port" --uno-port "$((port + 1))" \
        --user-installation "$profile" --conversion-timeout 7200 &
    pids+=("$!")
done
java -Xms256m -Xmx8g -XX:+ExitOnOutOfMemoryError \
    -Djava.awt.headless=true -Djava.io.tmpdir=/tmp/stirling-pdf \
    -jar /app/app.jar "$@" &
pids+=("$!")
# Any failed engine recycles only this container through its bounded restart policy.
wait -n "${pids[@]}" || true
exit 1
