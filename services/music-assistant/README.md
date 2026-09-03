# Music Assistant local-library source

This folder defines the narrow host-side share used by the official Music
Assistant app on Home Assistant OS. Music Assistant reads the same files as
Plex directly from the `9950x` music tree; Plex is not a Music Assistant
provider or dependency.

## Deployment

- Host: `9950x`
- Source: `/mnt/media/music`
- Client: Home Assistant OS at `192.168.50.75`
- Protocol: NFS on the trusted LAN
- Access: read-only with root squashing
- Music Assistant provider: `Filesystem (NFS share)`

Install the reviewed export definition and reload the NFS export table:

```bash
sudo install -o root -g root -m 0644 \
  services/music-assistant/music-assistant.exports \
  /etc/exports.d/music-assistant.exports
sudo exportfs -ra
sudo exportfs -v | grep -A1 '^/mnt/media/music'
```

Music Assistant should use server `192.168.50.50`, export path
`/mnt/media/music`, content type `Music`, and no subfolder. Do not add Plex as
a provider for this baseline.

## Verification

1. Confirm the export is limited to `192.168.50.75` and reports `ro` and
   `root_squash`.
2. Confirm the Music Assistant NFS provider loads and its initial sync reaches
   zero active tasks without provider errors.
3. Confirm the resulting track count is consistent with the files visible
   under `/mnt/media/music`.
4. Browse at least artists, albums, and tracks through the standard Music
   Assistant UI without starting playback unexpectedly.

## Recovery

The media remains owned by the existing `9950x` media-storage system and is
not backed up by Music Assistant. Music Assistant's database and provider
configuration are included in Home Assistant backups. To remove this source,
remove the Music Assistant provider first, then remove
`/etc/exports.d/music-assistant.exports` and run `sudo exportfs -ra`.

