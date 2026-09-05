# EdSys Time Machine collector and rehearsal lab

Owner: `9950x`; UI/API/history index: `/home/jeremy/code/edsys-ai-portal`.
The first scope is the AI Portal, its broker/database, persistent state,
container network, host, and Portal DNS. The collector only inspects production
containers and probes the loopback Portal health endpoint. It never reads
production database contents or executes a model request.

## Runtime contract

`time_machine.py serve` collects every five minutes and checks the fixed
rehearsal queue every five seconds. The user service restarts after failure and
holds a single worker lock. A restarted worker marks an unfinished rehearsal
interrupted, cleans only its labelled lab resources, and requires a new run.

Private shared state: `/opt/edsys-workhorse/edsys-ai-portal/data/time-machine/`.
The existing Portal `/data` mount exposes this as `/data/time-machine`. The
container receives no Docker socket or host credentials. The dedicated tree
uses group 1000, directories 2770, files 0660. The existing parent ACL allows
Jeremy to traverse the Portal state directory. Do not widen the parent access.

- `observations/`: immutable version-1 timestamped JSON, named by observation ID.
- `history.sqlite`: rebuildable Portal index and serialized request ledger;
  SQLite WAL with a busy timeout. The application validates provenance and graph
  integrity before indexing observations. Live and rehearsal histories are separate.
- `requests/`: only `{id, scenario}`; a 32-character hex ID and one fixed scenario.
- `results/`: bounded checks and final outcomes, retained across worker restarts.
- `heartbeat.json`: freshness signal; the API rejects new work when stale.

Raw observations, databases, queue files, lab fixtures, screenshots, and reports
are private runtime state and must stay out of Git, Obsidian, and managed RAG.
There is no automatic retention deletion in v1. Observe storage growth before
introducing a reviewed retention policy. The API exposes the latest 300
observations per stream, with the retained total, and the last 40 rehearsals.

## Lab containment

Scenarios: `dns`, `storage`, `release`, `healthy`, `ambiguous`.
Every run has a unique `edsys-tm-lab-<id>` name and ownership label. Docker
mutations are confined to those two containers and their internal network.
No host ports are published; no production volume, credentials or Docker socket
is mounted. Containers are unprivileged, read-only, capability-free, resource
limited, and use the installed Portal image by immutable image ID with
`--pull=never`. The small bind-mounted fixture contains synthetic records only.

Docker's default pools are exhausted on this host. The lab chooses a /28 in
`10.251.240.0/20` only after excluding overlaps with Docker networks and all
host routing tables. It does not change daemon pools or remove old networks.

DNS rehearsal changes only the fixture application's destination. Storage
rehearsal withdraws/restores the fixture SQLite file. Release rehearsal recreates
only the fixture application with a known failing release. Recovery must return
all baseline records and the exact original file SHA-256. Cleanup checks run
even after failure. Synthetic fixtures are retained privately after teardown.
The ambiguity control supplies a labelled synthetic symptom while withholding
dependency observations; it is not a real production failure.

## Install and verification

After source review and authoritative-main closeout:

```bash
bash services/time-machine/install.sh
systemctl --user is-active edsys-time-machine.service
python3 -m unittest discover -s services/time-machine/tests -v
systemd-analyze --user verify services/time-machine/edsys-time-machine.service
```

Portal UI: `#/time-machine`. Use its scenario selector to run an isolated
rehearsal and replay the baseline, fault, and recovered observations. The same
host CLI supports `collect` and `rehearse --scenario dns` while the service is
stopped; its worker lock prevents concurrent execution.

Rollback: stop and disable only `edsys-time-machine.service`, use the Portal's
transactional deployment rollback for the UI, and retain the private history.
Do not remove unrelated Docker networks or volumes. If interrupted, let the
worker reconcile its pending run or inspect the exact labelled lab resources.

## Backup and restore

The Portal state tree must remain in private/encrypted backups. Source files
alone cannot reconstruct observations from before collection began. Back up
SQLite with its online backup API or stop its writer before a consistent copy;
retain immutable observations and result files alongside it. To rebuild a
damaged index, retain a private copy of the database and its WAL, then rebuild
from observations/results; queued work must be reconciled before replay.
The worker stages `recovery/history.sqlite` through SQLite's online backup API
after collection, verifies integrity, and atomically replaces the prior stage.
The manifest records its timestamp and SHA-256. On 2026-09-05, the live backup
include list selects `/opt/edsys-workhorse`, no matching exclusion removes this
subtree, and the existing backup timer is enabled. This establishes coverage;
the next offsite snapshot containing the new history is still time-dependent.
No production restore is inferred from the lab's synthetic-data rehearsals.
