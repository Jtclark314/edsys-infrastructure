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
- Intended Drive mirror after enablement: `edsys-gdrive:EdSys Share`
- Intended version recovery after enablement: `edsys-gdrive:EdSys Share Recovery/<run-id>`

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
mount and both mount guards, creates or validates the locked, no-login POSIX
identity `edsys-share-dell`, validates the full Samba candidate with `testparm`,
and installs all systemd units. The renderer denies that restricted identity
from every other statically configured Samba service; disables registry,
usershare, default-service, auto-service, and printcap-created services; pins
standalone local authentication and TCP 445; clears identity maps and chroot
overrides; and refuses uninspected `include` or alternate `config file`
directives. The managed share fragment has an exact option allowlist and
explicitly resets inherited execution, DFS, VFS, ACL, and availability fields.
Before and after activation, the installer verifies that the reviewed vendor
`smbd` and `nmbd` units use no config/option override and that their live
process arguments are exact. A failed activation atomically restores the
timestamped prior Samba file and restarts it. Samba's guard is a compiled
no-shell validator run through a narrowly scoped AppArmor transition;
arbitrary shell execution does not get added to the `smbd` profile. Drive
timers remain disabled by default.

The installer never creates or records an SMB password. When onboarding the
managed work laptop, enable its Samba identity through the interactive hidden
prompt on `9950x`, then confirm that it is present. Never pipe, echo, or place the
password on a command line:

```bash
sudo smbpasswd -a edsys-share-dell
sudo pdbedit -L edsys-share-dell
```

The POSIX identity stays locked with `/usr/sbin/nologin`; only Samba has a
password verifier. A new or changed Samba share must be deployed through the
managed renderer so this identity cannot inherit access to it accidentally.

After creating a new Production Google OAuth desktop client, reauthorizing the
root-only `edsys-gdrive` remote, repairing the existing core offsite mirror,
reviewing the EdSys Share dry-run/initial seed, and testing a staged restore:

```bash
sudo scripts/edsys-share/install-9950x.sh --enable-backup
```

OAuth client IDs, client secrets, tokens, SMB passwords, status plans, logs,
and backup contents are runtime-private and must not enter Git or RAG.

## Guarded sync

Once every backup gate passes and the timers are explicitly enabled, the
15-minute job creates a local dry-run plan before every sync. It holds when the
initial seed has data, more than 100 deletions are proposed, or at least 25
percent of a remote with 20 or more files would change/delete.

Review a held plan locally, then approve that exact plan:

```bash
sudo /usr/local/sbin/edsys-share-gdrive-sync --approve RUN_ID
```

The command reruns the dry plan and refuses approval if its SHA-256 changed.
Overwritten/deleted objects move to a timestamped recovery directory. When
enabled, the weekly prune only operates after a verification within 24 hours
and sends strictly named recovery directories older than 90 days to Drive
Trash. It never calls `rclone cleanup`.

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

Run in the normal non-elevated Windows desktop profile. Windows PowerShell 5.1
on the initial clients returned error 87 for the `New-SmbMapping -Credential`
overload, so save the server credential through `cmdkey`'s explicit hidden
prompt first, then create the mapping without passing a secret to PowerShell:

For Nimo `jtcla` and Basecamp `jeremy`:

```powershell
cmdkey /add:9950x.taile832fe.ts.net /user:9950x\jeremy /pass
New-SmbMapping -LocalPath 'Q:' `
  -RemotePath '\\9950x.taile832fe.ts.net\EdSys-Share' `
  -Persistent $true -RequireIntegrity $true -RequirePrivacy $true
```

The `/pass` switch has no value on purpose and prompts without displaying or
recording the password in command history.

For work-laptop profile `THOMPSON\jclark` only:

```powershell
cmdkey /add:9950x.taile832fe.ts.net /user:9950x\edsys-share-dell /pass
New-SmbMapping -LocalPath 'Q:' `
  -RemotePath '\\9950x.taile832fe.ts.net\EdSys-Share' `
  -Persistent $true -RequireIntegrity $true -RequirePrivacy $true
```

Nimo `jtcla` and Basecamp `jeremy` authenticate as `9950x\jeremy`. The managed
work-laptop profile `THOMPSON\jclark` instead uses the dedicated SMB-only
`9950x\edsys-share-dell` identity. Samba forces that identity to filesystem
user `jeremy` only after successful authentication, preserving consistent
ownership while allowing Dell-only credential rotation and revocation.
Every other currently configured Samba service explicitly denies the Dell
identity. The managed renderer must be rerun whenever a service is added.

## Revoke Only The Work Laptop

This bounded path must not change Nimo/Basecamp credentials or mappings.

1. In `THOMPSON\jclark`, disconnect `Q:` and delete only the matching FQDN
   Credential Manager entry:

   ```powershell
   Remove-SmbMapping -LocalPath 'Q:' -Force -UpdateProfile
   cmdkey /delete:9950x.taile832fe.ts.net
   ```

2. Disable new work-laptop authentication, stop the Tailnet socket, remove only
   `100.84.178.87` from the private runtime allowlist, reload the guard, and
   reopen the socket:

   ```bash
   sudo smbpasswd -d edsys-share-dell
   sudo systemctl stop edsys-share-tailnet-smb.socket
   sudoedit /etc/edsys-share/edsys-share.conf
   sudo systemctl restart edsys-share-tailnet-guard.service
   sudo systemctl start edsys-share-tailnet-smb.socket
   ```

3. Verify the nftables set no longer contains that address. Use
   `sudo smbpasswd -x edsys-share-dell` only for permanent removal, after its
   sessions are gone. Remove the locked POSIX identity only after no Samba
   configuration references it.

## Full Rollback

1. In each affected normal Windows profile, remove `Q:` with
   `Remove-SmbMapping -LocalPath 'Q:' -Force -UpdateProfile`. Delete the saved
   FQDN credential only if that profile has no other `9950x` mapping using it;
   preserve the shared Nimo/Basecamp `jeremy` credential. The dedicated Dell
   entry can be removed with `cmdkey /delete:9950x.taile832fe.ts.net`.
2. Disable all four Drive timers plus `edsys-share-tailnet-smb.socket` and
   `edsys-share-tailnet-guard.service`; remove only table `inet edsys_share`.
3. Restore the timestamped Samba backup, validate it with `testparm`, and
   restart only `smbd` and `nmbd`.
4. Remove the managed EdSys Share block from the local `smbd` AppArmor file,
   restore its backup if applicable, remove the dedicated preexec profile, and
   reload AppArmor.
5. Unmount `/EdSys-Share`, then restore the timestamped `/etc/fstab` backup and
   run `systemctl daemon-reload`.
6. Disable/remove the `edsys-share-dell` Samba verifier before removing its
   locked POSIX identity. Do not change the shared Nimo/Basecamp `jeremy`
   credential.
7. Preserve `/mnt/ai-store/edsys-share` and both Drive trees unless their
   deletion is separately authorized.
