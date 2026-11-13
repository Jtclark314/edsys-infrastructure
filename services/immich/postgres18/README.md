# Immich PostgreSQL18 image and major migration

Production qualified November 13, 2026: PostgreSQL18.7, pgvector0.8.7 and
VectorChord1.1.1. PostgreSQL19 remains a release candidate, so18 is the newest
stable major. Immich3.4.1 documents PG14–19, pgvector>=0.7,<0.9 and
VectorChord>=0.3,<2 compatibility. See the official
[PG release announcement](https://www.postgresql.org/about/news/postgresql-187-1712-1616-1520-1425-and-19-rc-2-released-3442/)
and [Immich database requirements](https://docs.immich.app/administration/postgres-standalone/).

The pinned upstream `pgvector/pgvector:0.8.7-pg18-bookworm` base already supplies
PG18.7-1.pgdg12+2 and pgvector0.8.7. Add only the upstream native PG18 amd64
VectorChord1.1.1 package; its release SHA256 is in `SHA256SUMS`. Download it from
[the immutable upstream release](https://github.com/supervc-stack/VectorChord/releases/download/1.1.1/postgresql-18-vchord_1.1.1-1_amd64.deb)
into a private build context beside `Dockerfile`. Verify the hash before
building with `--network=none --pull=false` after caching the pinned base.
The package depends on PG18/libgcc/libc>=2.35; Bookworm2.36 fits. No PG14
binary is reused. Packages/logs/image exports/data remain outside Git.

Accepted local image: `sha256:51c86899fb517315e40e3c33fc9196c752ac82e11cf8f242511a5ed2cec27f2c`.
The exact offline archive is retained separately on NVMe and selected for
encrypted recovery:171,261,440 bytes, SHA256
`65131c851617894d597640bfe59eba64cf698211789c732f8bf7998b1326d5f9`.
All23 OCI blob hashes and complete index/manifest/config/layer references
passed, so Docker-disk loss does not remove the only image copy. The full
base layers, recipe and verified extension package remain recoverable.

## Layout, startup and fallback

Bind the NVMe `immich/postgres` parent to `/var/lib/postgresql`; actual
`PGDATA=/var/lib/postgresql/18/docker`. Its parent must permit traversal by the
image's postgres UID999. The root-private `storage.env` must explicitly set
`IMMICH_POSTGRES_MAJOR=18`, and the installed unit/guard validate the existing
versioned cluster/control file on the exact mounted filesystem. Preload only
`vchord.so`. Normal `v3.4.1-cpu.json` and `v3.4.1-cuda.json` both retain this
database image/layout, including CUDA→CPU fallback. An omitted major in a
3.4.1 profile also selects18; PG14 requires an explicit recovery profile.

## Qualified migration and recovery boundary

The audited old cluster had no legacy `vectors` extension/schema/types/columns;
the unused `vectors.so` preload therefore carries no target dependency. A
logical restore into a separate initialized18 cluster rebuilt every index
with current native extensions. Locale/encoding and data checksums were retained.
All85 application tables / 377,090 rows matched normalized hashes before app
startup, with real CPU app/ML reads and both CLIP/face index queries passing.
Only then were app writers stopped for a fresh dump and matched cold14.25
database/Redis/config/server-data checkpoint. The fresh production restore
also matched all 85 tables before guarded cutover. The original live14 directory
and exact old image remain retained independently of that checkpoint.

Production preserved all 11,607 assets, one owner, six sessions, 7,758 CLIP and
1,965 face rows, persisted configuration and original references. The normal
version-check `checkedAt` timestamp advanced; other application data matched.
Actual CUDA semantic/visual inference and byte-matched thumbnails passed.
The effective installed backup adapter accepts only the reviewed14/18 NVMe
layouts and rejects wrong-major/shadow mounts. Its actual final collector
stage passed; the post-major dump was independently restored with matching
table hashes and working vector indexes. Existing schedules/policies stay intact.

PG18→PG14 rollback requires the matched fresh cold14.25 data, Redis/config,
old image and expected-major14 EnvironmentFile/guard together. Use
`v3.4.1-pg14-cpu.json` or `v3.4.1-pg14-cuda.json`; changing a tag against the
new data is unsafe. Protect subsequent uploads/account changes first.
Application3.4.1→3.3.5 recovery has a different matched pre-app-upgrade checkpoint.
