# Immich PostgreSQL 14 maintenance image

This bounded derivative starts from the exact qualified Immich PostgreSQL
14.19/vector image. It updates only PostgreSQL 14 server/client to 14.25 and
their required `libpq5` client library to 18.7. PostgreSQL major 14, existing
LLVM, every other installed package, and every vector extension binary,
control file, and migration script remain unchanged. The build checks those
invariants and performs no network requests.

Fetch the three `.deb` filenames in `SHA256SUMS` from the corresponding
`pool/main/p/postgresql-14/` or `pool/main/p/postgresql-18/` directory beneath
`https://apt.postgresql.org/pub/repos/apt/`. Verify the PGDG Bookworm
`InRelease` signature, its SHA256 for `main/binary-amd64/Packages.gz`, and the
package hashes before placing them beside `Dockerfile` and `SHA256SUMS` in a
private build context. Packages, signatures, build logs, image exports,
databases, and recovery records stay outside Git/RAG.

Build with `--network=none`; no ports, mounts, accounts, or service settings
change. Qualify the resulting immutable image by restoring the actual
pre-upgrade Immich dump into an isolated cluster, checking extension versions,
asset/reference and sign-in state, then starting the matching application.
Retain the previous image and a matched cold PostgreSQL/configuration
checkpoint before promotion. Never run two servers against the same PG data.

This image is now retained for matched pre-major recovery through the explicitly
named `v3.4.1-pg14-*` profiles. Normal production and CPU fallback use the
qualified PostgreSQL18.7 image in `../postgres18/README.md`. PostgreSQL14's
support ended November 12, 2026; the final14.25 patch does not extend it.
