# EdSys Share Host Tooling

Fresh host-native tooling for the root-level `/EdSys-Share` file share on
`9950x`. It does not import or reuse the retired Courier application, source,
accounts, ports, database, client, Tailscale configuration, or deployment
templates.

## Layout

- Backing storage: `/mnt/ai-store/edsys-share`
- Canonical path: `/EdSys-Share` (persistent bind mount)
- Samba share: `EdSys-Share`
- Tailnet listener: a dedicated systemd socket on the 9950x Tailnet IP,
  forwarded by `systemd-socket-proxyd` to Samba loopback
- Tailnet client restriction: dedicated nftables base chain generated from
  `EDSYS_SHARE_TAILNET_CLIENTS`
- Current Drive mirror: `edsys-gdrive:EdSys Share`
- Version recovery: `edsys-gdrive:EdSys Share Recovery/<run-id>`

Samba remains bound to `lo enp7s0`. Samba 4.19 does not reliably bind a
point-to-point/non-broadcast Tailscale interface while `bind interfaces only`
is enabled. The generic systemd socket forwarder preserves strict Samba
binding, and the nftables chain filters source Tailnet IPs before the socket.

## Install

Review `edsys-share.conf.example`, especially the current host and client
Tailnet addresses. Then:

```bash
sudo scripts/edsys-share/install-rclone-1.74.4.sh
sudo scripts/edsys-share/install-9950x.sh
```

Validate source before installation with:

```bash
scripts/edsys-share/test-edsys-share.sh
```

The installer backs up `/etc/fstab` and `/etc/samba/smb.conf`, creates the bind
mount and mount guard, validates the full Samba candidate with `testparm`, and
installs all systemd units. Drive timers remain disabled by default.

After creating a new Production Google OAuth desktop client, reauthorizing the
root-only `edsys-gdrive` remote, repairing the existing core offsite mirror,
reviewing the EdSys Share dry-run/initial seed, and testing a staged restore:

```bash
sudo scripts/edsys-share/install-9950x.sh --enable-backup
```

OAuth client IDs, client secrets, tokens, SMB passwords, status plans, logs,
and backup contents are runtime-private and must not enter Git or RAG.

## Guarded sync

The 15-minute job creates a local dry-run plan before every sync. It holds when
the initial seed has data, more than 100 deletions are proposed, or at least
25 percent of a remote with 20 or more files would change/delete.

Review a held plan locally, then approve that exact plan:

```bash
sudo /usr/local/sbin/edsys-share-gdrive-sync --approve RUN_ID
```

The command reruns the dry plan and refuses approval if its SHA-256 changed.
Overwritten/deleted objects move to a timestamped recovery directory. The
weekly prune only operates after a verification within 24 hours and sends
strictly named recovery directories older than 90 days to Drive Trash. It
never calls `rclone cleanup`.

Restore either the current mirror or one recovery run into a new root-only
staging directory. The helper verifies the staged copy by checksum and writes
a local SHA-256 manifest; it never writes back into `/EdSys-Share`:

```bash
sudo /usr/local/sbin/edsys-share-gdrive-restore --current
sudo /usr/local/sbin/edsys-share-gdrive-restore --recovery RUN_ID
sudo /usr/local/sbin/edsys-share-gdrive-restore --recovery RUN_ID --path 'Folder/file.mp4'
```

Staging defaults to AI Store rather than the system SSD, reserves 10 GiB of
free space, and supports a selected relative path to avoid restoring an entire
large tree unnecessarily.

## Windows mapping

Run in the normal non-elevated Windows desktop profile, entering the existing
Samba `jeremy` password only in the interactive credential prompt:

```powershell
$cred = Get-Credential -UserName '9950x\jeremy'
New-SmbMapping -LocalPath 'Q:' `
  -RemotePath '\\9950x.taile832fe.ts.net\EdSys-Share' `
  -Credential $cred -Persistent $true -SaveCredentials -RequirePrivacy $true
```

The initial profiles are Nimo `jtcla` and Basecamp `jeremy`. Dell work-laptop
identity and credential policy remain intentionally deferred.

## Rollback

1. Disable Drive timers and `edsys-share-tailnet-smb.socket`.
2. Remove only table `inet edsys_share`.
3. Restore the timestamped Samba/fstab backups.
4. Unmount `/EdSys-Share`.
5. Preserve `/mnt/ai-store/edsys-share` and both Drive trees unless their
   deletion is separately authorized.
