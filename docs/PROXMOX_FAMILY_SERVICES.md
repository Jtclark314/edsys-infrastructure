# Family services VM202 memory and recovery

## Scoped capacity repair — 2026-10-20

`family-services` remains VM202 on `pve-node2`, with four vCPU and its existing
host CPU model, LAN adapter and local disks. Its active RAM allocation is
**5120 MiB**, reduced from 13312 MiB on a host with 7861 MiB usable physical
RAM. Ballooning remains disabled. The existing effective virtual board
`pc-q35-10.0+pve0` is now explicitly pinned, preventing the formerly floating
`q35` setting from silently choosing a newer board on future cold starts.
No guest migration, disk/network/CPU change or host reboot was performed.

The original fixed allocation caused host overcommit: QEMU had several GiB
swapped, and five old guest daemons waited in KVM asynchronous-page faults
despite ample guest-visible available memory. A preparatory full-file hash
read increased pressure by touching more guest pages; that owned process was
stopped. Do not pre-read a multi-GiB file tree on an already overcommitted
fixed-memory VM just to prepare a memory repair.

The owner confirmed Nextcloud, Immich and Paperless were not in use. A normal
120-second shutdown timed out with guest networking and guest agent stopped.
A native no-RAM snapshot captured all three LVM-backed disks and VM config
while the guest was quiescent in late shutdown; filesystem freezing was skipped
because the agent was stopped. This is an on-disk checkpoint, not a saved RAM
image or a newly qualified application-consistent full guest restore.
A controlled VM-only stop completed the stalled shutdown, then the smaller
allocation and preserved board were applied and the VM started. An initial
6144 MiB candidate restored the apps, but full original-file copying showed
insufficient hypervisor headroom as guest page cache filled. After the copy
was verified and idle gates repeated, one further **graceful** guest restart
applied the final 5120 MiB allocation. No second disk snapshot was created,
preserving the original pre-change checkpoint under the retention policy.

The installed QEMU binary changed naturally from the old running 10.0.2 process
to installed 11.0.3; the board remained 10.0. Guest boot used already-installed
kernel 6.8.0-147. No packages were installed in this repair. Startup reported an
older EFI certificate enrollment warning; certificates were not modified.

## Verification

- Live VM status/configuration confirm 5120 MiB and the preserved 10.0 board.
- Guest agent/SSH respond, all twelve containers run and configured checks pass.
- Nextcloud reports installed, maintenance off and no database upgrade needed;
  Immich ping responds and Paperless serves its normal sign-in redirect.
- All three PostgreSQL databases respond. Selected record counts match the
  consistent pre-change dumps exactly; no schema migration was attempted.
- Old KVM page-fault waits and failed systemd units are absent; guest swap is
  zero and current host memory-pressure samples returned to zero. Historical
  host swap occupancy is not itself evidence of continuing thrashing.

Do not claim a new upload, photo-processing, document-consumption or full
application restore test: those were not initiated during maintenance.

## Protected recovery and retention exception

Private off-host recovery preserves the original/current VM configuration,
three independently consistent PostgreSQL custom-format dumps with role exports,
private application definitions/configuration and volume mappings. Each database
dump was fully decoded with `pg_restore`; transferred files were hash-verified.
The completed original-file capture has 29,775 file/symlink records, with all
29,648 regular files SHA-256 hashed (6,063,516,930 bytes), zero missing records
and no pre-copy metadata changes. It is archived in root-private selected
backup staging to avoid bulk-media exclusions dropping original videos.
A fresh full encrypted local backup (without pruning) includes these recovery
artifacts. An isolated file restore passed Restic verification; the originals
archive and database/final-configuration archive hashes match their protected
sources. This verifies file recovery, not a full application restore. The new
offsite copy remains pending the existing mirror schedule; older offsite
backups remain intact.
Live file copies plus independent database dumps are not a single cross-app
transaction. None of these files or credentials belongs in Git/RAG.

The fresh native snapshot `pre-memory-6144-20261020T1912Z` covers all three disks
and the original RAM configuration. The existing
`edsys-proxmox-snapshot-retention.path`/timer policy keeps only the newest
snapshot per guest: it automatically removed the older June checkpoint during
this operation. That earlier snapshot is **not retained**. The new checkpoint
was verified present; the deletion was traced to the existing retention
service, not an explicit operator snapshot-delete command. Existing encrypted
backups were not pruned or replaced by this repair.

For future maintenance, inspect retention triggers **before** creating a
snapshot, and secure any required historical rollback independently of the
one-snapshot policy. A local thin snapshot is not an off-host backup.

For an allocation regression, first confirm the guest is idle and preserve any
newer data, then gracefully stop the VM and restore the prior 13312 MiB setting
if necessary, retaining the known board. That restores the previous capacity
problem and is a temporary fallback. Do not roll back disks/databases merely
to undo a RAM setting. A disk snapshot restore requires a separate need and
protection of post-checkpoint application data.
