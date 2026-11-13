FROM docker.io/pgvector/pgvector:0.8.7-pg18-bookworm@sha256:38681dc6eaf55308e2a4c6de392d131652dcfac70e1263e35cd9b5ea3fe39782
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY postgresql-18-vchord_1.1.1-1_amd64.deb /tmp/vchord.deb
RUN echo '7637a18a97157db8d904bb7093e5ce33b6a395c0acac9ae6b660032f3d57a33e  /tmp/vchord.deb' | sha256sum -c - \
    && dpkg -i /tmp/vchord.deb \
    && rm /tmp/vchord.deb \
    && test "$(postgres --version)" = 'postgres (PostgreSQL) 18.7 (Debian 18.7-1.pgdg12+2)' \
    && test -f /usr/lib/postgresql/18/lib/vchord.so \
    && test -f /usr/share/postgresql/18/extension/vector--0.8.7.sql
CMD ["postgres", "-c", "shared_preload_libraries=vchord.so"]
