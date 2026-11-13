# Workhorse AI maintenance helpers

`s3_migrate.py` is an explicit operator tool for the 2026-11-12 Langfuse
MinIO-to-SeaweedFS migration. It installs no service, listener or automation and
does not change application endpoints. The old MinIO service/image/data remain
the recovery source until separate application acceptance and retirement.

The helper accepts only non-versioned source buckets with a private owner-only
ACL. Enabled/suspended versioning, object locks, non-default ACLs and objects
larger than 5 GiB are refused rather than silently losing semantics. It copies
applicable bucket policy/CORS/lifecycle/encryption/tags and object content,
metadata, tags and common content headers. The commissioned source had one
non-versioned bucket and no custom bucket configurations.

Use a private outside-Git SeaweedFS identity JSON file containing the existing
S3 credential pair. Credentials are read in process, never passed on the CLI or
written to receipts. Discover current private Docker IPs immediately before each
phase; a container recreation may change them. Do not expose an S3 listener or
upload real objects to a third-party service.

```sh
python s3_migrate.py --source http://SOURCE_PRIVATE_IP:9000 \
  --destination http://DESTINATION_PRIVATE_IP:8333 \
  --credentials /private/seaweed-identity.json \
  --evidence /private/migration-evidence --phase initial --workers 2
```

Initial copy can run while the application writes to the old store. Its SQLite
ledger allows resume after interruption; it never mutates the source. Temporary
object buffers spool above 8 MiB into the private evidence directory. Each new
copy is read back and verified with content SHA-256, size, metadata and tags.
ETags are used only as an initial-resume hint.

After the operator stops all old application writers, run `--phase final
--writers-quiesced`. This assertion is required; the helper does not itself stop
or inspect application writers. Final mode rereads source and destination bytes
for every object and compares SHA-256 plus metadata/tags, copying any difference.
It removes an obsolete destination object only when its ledger proves it was
previously migrated and its current bytes still match that record. Unknown or
changed destination extras require review. Source data remains untouched.

Both phases print aggregate progress only. Object names, configuration and raw
state stay in 0700/0600 operator evidence outside Git. The final summary requires
an exact bucket match and no unmatched destination objects. Retain the private
ledger and summaries with the application migration/recovery checkpoint.

An explicitly owned rehearsal bucket can remain separate through
`--allow-extra-bucket NAME`; it is excluded from source equality and never
deleted. Final mode must still exactly match every migrated source bucket.

Acceptance also requires an authenticated byte/metadata/tag roundtrip,
anonymous/invalid-key denial, persistence through a destination restart, and
Langfuse historical reads/new trace writes after endpoint switching. S3 equality
alone does not establish database/schema or end-user application acceptance.

Run tests using the isolated maintenance environment containing boto3:

```sh
python -m unittest discover -s services/workhorse-ai-maintenance -p 'test_*.py' -v
ruff check services/workhorse-ai-maintenance
```

## Recovery enrollment

`database_backup.py` stages both PostgreSQL roles/databases with `pg_dumpall`
and a native ClickHouse `BACKUP DATABASE default`. The reviewed backup disk
configuration is in `docker/9950x-workhorse/config/clickhouse/backup_disk.xml`;
the container writes temporary native archives only under the dedicated
`/mnt/ai-store/langfuse/clickhouse-backups` bind. SHA-256 is checked after copying
each archive into private staging, then that exact temporary archive is removed.
Diagnostic ClickHouse system logs remain in the original volume.

The `69-workhorse-ai-databases.conf` drop-in runs this stage before the S3
drop-in. Each PostgreSQL database dump and the native ClickHouse database backup
is consistent independently; they are not an atomic cross-service transaction.
The append-only S3 export follows them so pre-checkpoint object references are
covered. The coordinated stopped commissioning checkpoint remains the stricter
recovery boundary. The database stage keeps completed current/previous
generations under `/srv/edsys-backup/staging/workhorse-ai-databases` and refuses
unowned or symlinked generation paths. A failed run leaves the prior completed
generation intact and does not publish a partial backup.

The native ClickHouse archive was restored to an isolated database on the
qualified server version, with all nine historical data tables and archive
hashes verified. `checkpoint.py` remains the separate operator command for a
quiesced pre-migration PostgreSQL/ClickHouse Native export; it refuses running
Langfuse web or worker containers. Keep that export with the matching stopped
Seaweed native checkpoint and migration ledger.

```sh
sudo python3 services/workhorse-ai-maintenance/database_backup.py
# Restore PostgreSQL into a new private server, then restore ClickHouse into a
# fresh database using the same backup disk and qualified image:
# RESTORE DATABASE default FROM Disk('edsys_backups', 'langfuse-clickhouse.tar');
```

`install.py` installs the seven-distribution maintenance runtime from the
complete hashed lock at `/home/jeremy/.local/share/edsys-workhorse-maintenance/`.
It changes no global packages and installs no timer or network service.

`edsys-backup-langfuse-s3.conf` is a drop-in for the existing encrypted backup.
It runs `s3_backup.py` as root using that isolated runtime. The helper discovers
the current production Seaweed private IP and streams every object through S3
into a private `objects.tar`, retaining keys, headers, metadata, tags, bucket
configuration, exact identity config and source image identity. Actual bytes are
SHA-256 checked after export. It does not copy live LevelDB/raft/volume files.

The export is a recoverable object set captured over an explicit time interval,
not a global application/database point-in-time transaction. Scheduled exports
protect object recovery while the separate native stopped checkpoint and
coordinated database checkpoints provide the commissioning rollback boundary.
No live-file tar is labelled consistent.

The stage is `/srv/edsys-backup/staging/langfuse-s3/`, already covered by the
existing staging include. Temporary exports live under its globally excluded
`tmp/`; only verified exports are atomically published, with current/previous
staging retained. Canonical private migration manifests are kept under
`/opt/edsys-workhorse/langfuse/recovery-manifests/` and copied into exports.
Original keys/identity and raw manifests never enter Git or general RAG.

```sh
sudo /home/jeremy/.local/share/edsys-workhorse-maintenance/venv/bin/python \
  services/workhorse-ai-maintenance/s3_backup.py
python services/workhorse-ai-maintenance/s3_backup.py --verify /private/objects.tar
```

`seaweed_checkpoint.py capture` refuses a running Seaweed container or any
explicitly listed running production/rehearsal writer. After the operator's
coordinated stop, it captures native data, exact identity, image/command, the
SQLite-consistent frozen object ledger and file hashes. Keep this checkpoint in
the existing backup stage at `/srv/edsys-backup/staging/langfuse-s3-native/`.

`seaweed_checkpoint.py restore` verifies/extracts into a fresh private directory,
starts the same pinned image on a new internal Docker network with the same
private DNS alias and no host ports, then verifies all restored production
objects' SHA-256, sizes, headers, metadata, tags and roster against the frozen
ledger. It removes only its temporary container/network and preserves restore
evidence. Never attach the live production volume to the disposable verifier.

```sh
sudo python seaweed_checkpoint.py capture --output /private/stopped-checkpoint \
  --evidence /private/migration --writer PRODUCTION_WEB --writer PRODUCTION_WORKER \
  --writer REHEARSAL_WEB --writer REHEARSAL_WORKER
sudo python seaweed_checkpoint.py restore --checkpoint /private/stopped-checkpoint \
  --work /private/new-isolated-restore --network-subnet 10.254.249.0/28
```

The restore subnet is required and checked against current Docker networks and
host routes before creation. Choose a fresh private IPv4 range; the example was
available during commissioning and must be rechecked for a future restore.
The verifier uses no default address pool and never removes unrelated networks.

The November 13 commissioning checkpoint passed a full native restore of
142,415 production objects / 804,101,811 bytes, with all 144 native-file hashes
verified. That retained checkpoint also contains the explicitly owned
`langfuse-rehearsal-20261112` bucket; add
`--allow-extra-bucket langfuse-rehearsal-20261112` when restoring this specific
checkpoint. Production cleanup removed its 37 test objects afterward, with all
seven production data files and seven indexes unchanged both across cleanup and
against the proven checkpoint. The live roster then contained only `langfuse`.
The original full restore is the commissioning proof; a cleanup-only capture is
not represented as a second full-object restore. Recurring logical exports use
the clean production roster.

Install/enroll the backup drop-in only after final source equality and cold
restore acceptance. A subsequent encrypted/offsite snapshot is a separate
root-owned closeout check; local staging alone does not establish offsite recovery.

See [S3 compatibility](S3-COMPATIBILITY.md) for the verified SeaweedFS MIME
inference edge. Explicit octet-stream content on a `.json` key can be inferred
as JSON. The actual Langfuse objects use compatible content types and still
require exact header/content/metadata/tag equality. The tools refuse mismatches.
