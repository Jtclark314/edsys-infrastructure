FROM ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23

# The build context contains only these PGDG packages and their reviewed hashes.
# No production configuration, database, credentials, or media enter this image.
COPY SHA256SUMS *.deb /tmp/edsys-postgres14-patch/

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Preserve the inherited runtime working directory; cd is confined to this step.
# hadolint ignore=DL3003
RUN set -eu; \
    mkdir -p /usr/local/share/edsys-postgres14-patch; \
    find /usr/lib/postgresql/14/lib /usr/share/postgresql/14/extension \
      -maxdepth 1 -type f \( -name 'vector*' -o -name 'vchord*' \) \
      -print0 | sort -z | xargs -0 sha256sum \
      > /usr/local/share/edsys-postgres14-patch/extensions.sha256; \
    test -s /usr/local/share/edsys-postgres14-patch/extensions.sha256; \
    dpkg-query -W -f='${binary:Package}\t${Version}\n' \
      | sort > /tmp/packages-before; \
    cd /tmp/edsys-postgres14-patch; \
    sha256sum -c SHA256SUMS; \
    dpkg -i libpq5_18.7-1.pgdg12+2_amd64.deb \
      postgresql-client-14_14.25-1.pgdg12+1_amd64.deb \
      postgresql-14_14.25-1.pgdg12+1_amd64.deb; \
    test -z "$(dpkg --audit)"; \
    sha256sum -c /usr/local/share/edsys-postgres14-patch/extensions.sha256; \
    dpkg-query -W -f='${binary:Package}\t${Version}\n' \
      | sort > /tmp/packages-after; \
    sed -E '/^(postgresql-14|postgresql-client-14|libpq5(:amd64)?)[[:space:]]/d' \
      /tmp/packages-before > /tmp/unrelated-before; \
    sed -E '/^(postgresql-14|postgresql-client-14|libpq5(:amd64)?)[[:space:]]/d' \
      /tmp/packages-after > /tmp/unrelated-after; \
    diff -u /tmp/unrelated-before /tmp/unrelated-after; \
    cp SHA256SUMS /usr/local/share/edsys-postgres14-patch/packages.sha256; \
    cp /tmp/packages-before /tmp/packages-after /usr/local/share/edsys-postgres14-patch/; \
    postgres --version | grep -F 'PostgreSQL) 14.25'; \
    pg_dump --version | grep -F 'PostgreSQL) 14.25'; \
    rm -rf /tmp/edsys-postgres14-patch /tmp/packages-before /tmp/packages-after \
      /tmp/unrelated-before /tmp/unrelated-after

ENV PG_VERSION=14.25-1.pgdg12+1
LABEL org.opencontainers.image.title="EdSys Immich PostgreSQL 14 maintenance patch" \
      org.opencontainers.image.version="14.25-vectorchord0.4.3-pgvectors0.2.0"
