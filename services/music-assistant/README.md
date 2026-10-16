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

## Bedroom Pi music and audiobooks — 2026-10-15

Reuse the official Music Assistant HAOS app, version 2.10.6, and the retained
9950x Audiobookshelf container, version 2.34.0. Neither server runs on the Pi.
Kodi remains the Pi player and uses the existing HDMI connection to LG/soundbar.
No Pi receiver, Kodi add-on, listener, audio owner or reboot was added.

| Component | Owner and access |
| --- | --- |
| Music Assistant | HAOS app; HA sidebar/ingress; direct LAN UI `http://192.168.50.75:8095/` |
| Audiobookshelf | Existing Compose `/srv/ssd1/docker/stacks/audiobookshelf/docker-compose.yml`; LAN UI `http://192.168.50.50:13378/` |
| Books and progress | Existing `/mnt/media/audiobooks`, ABS config/metadata mounts, existing owner account |
| Pi player | MA Home Assistant MediaPlayers provider, only the existing native Kodi Pi entity selected; display name **Bedroom Pi Plex** |
| HA controls | **Bedroom Pi → Music & books**, `/edsys-pi/music-books`; existing Pi transport and LG volume/input controls |

Music Assistant's existing Home Assistant plugin supplies the outbound player
connection. The separate Music Assistant integration in HA remains user-disabled;
there is no global re-enable, duplicate entity import or change to existing players.
Keep its old link-only preference intact. Other music providers and household
outputs are preserved.

Add the Audiobookshelf music provider with the existing server LAN address and a
proper ABS API key, not a deprecated legacy login token. Use the existing owner
account so progress stays with that account; do not reset its password. Store the
key only in protected runtime configuration. The official provider synchronizes
books and listening progress. Music Assistant browses and sends media through HA
to Kodi; the separate ABS web player plays on the browser device.

Select **Bedroom Pi Plex** in Music Assistant before choosing music or a book.
A new selection replaces current Pi playback, including Plex. Volume/input stays
with the existing LG remote. Do not start playback as a connectivity check while
the owner is watching. The native Pi dashboard media card and explicit transport
buttons address the same endpoint. Track/queue navigation and audiobook position
belong in Music Assistant, which owns the streamed queue; the new view does not
misroute Next/Previous to Kodi’s separate local queue.

### Verification and limits

Authenticated browsing returned artists, albums, tracks and the existing audiobook.
The MA library contains 10,074 music tracks and one audiobook. The ABS item has
8 valid audio files/tracks and no missing/invalid flag. Both new providers are
loaded without a reported error and the sole selected Pi player is available.
The dashboard save was read back exactly. Ordered recovery audit and dry-run
passed with Audiobookshelf running; no restart drill was performed.

Music/book audibility, track changes, audiobook seeking/resume and interruption
acceptance are pending until the owner's show ends. Phil's installed reply
arbitration currently supports internal video sessions only. Do not claim music
or audiobook speech interruption/return is implemented or accepted. Library
selection by voice also needs a separate bounded integration. No playback,
input/power, overlay, microphone or HA/Pi restart command was used for setup.

### Recovery

Before reactivation, ABS config/metadata, Compose definition and container identity
were backed up privately with SQLite integrity checked. MA settings/options plus
consistent library/auth SQLite backups were also checked before adding providers.
Recovery artifacts and any credentials remain outside Git/RAG.

For a narrow rollback, remove only the new ABS and HA MediaPlayers provider
instances after confirming they have not acquired later user changes; revoke
only this integration's ABS API key. Restore the single new dashboard view/link
by comparison. Do not restore MA's whole database over subsequent listening
progress or remove original media. To cold-stop ABS again, remove only its
`bedroom-media` tier from the ordered recovery manifest before stopping that
existing container. Keep config, metadata and media. Its current `unless-stopped`
policy and existing Compose labels are retained, with `/mnt/media` gating recovery.

## Dashboard access and player selection — 2026-10-16

Use the consistent teal **Music Assistant** native shortcut, with the description
**Music & audiobooks**, in Home's Now Playing, the four indoor TV rooms in Spaces,
Media overview and its four remote/player views, and Bedroom Pi's Player,
Music & books, Night & art and LG remote. Four dashboards have fourteen placements
across eleven views. Existing Music launchers are replaced, avoiding duplicates;
existing TV, Plex, scene, lighting and other cards remain in order.

The target is the existing HA app route `/app/d5369777_music_assistant`.
The old `/hassio/ingress/…` frontend route is not registered on this HA version;
it redirects to the default dashboard. Use the live `app` panel instead.
Navigation opens MA inside HA using the current HA session, including remote HA
access; it does not rely on a browser reaching the private direct MA LAN URL.
No player is selected automatically and navigation sends no playback, input,
volume or power command. Native shortcut appearance is owned by HA; no custom
frontend resource or theme is required.

In MA, click the **current device name at the bottom right** to open **Players**,
then select **Bedroom Pi Plex**. The neighboring **1 Player / Group members**
control manages the current group and is not the full output selector. On
October 16 the actual Nimo Chrome output picker contained the enabled, available,
non-hidden Pi. Selecting it changed the visible current output to Bedroom Pi
Plex without starting media; Kodi remained paused at the same position. No
provider repair/reload/recreation was needed. Merely listing a player through a
privileged API is not sufficient evidence of visibility in the user's picker.

`dashboard_access.py` produces native shortcuts and a narrow, idempotent candidate
against explicit existing sections. It saves private before/after JSON, compares
all targets before the first save, compares each again before its write, and
requires exact readback. An unknown/partial result requires inspection rather
than replay. Review the dry run before applying:

```bash
python3 services/music-assistant/dashboard_access.py --private-state PRIVATE_DIR
python3 services/music-assistant/dashboard_access.py --private-state PRIVATE_DIR --apply
```

Keep all live JSON private. For rollback, compare each current dashboard to its
saved after-version and restore only these launchers and the output-picker help
paragraph, preserving later user edits. A stale checkpoint must not be forced over
a newer dashboard. Pi's full-dashboard builder reuses the same shortcut definition.

References: [HA native shortcut card](https://www.home-assistant.io/dashboards/shortcut/),
[MA player settings](https://www.music-assistant.io/settings/individual-player/).

Desktop verification on Nimo showed the native teal tile in Home and the Master
Bedroom remote. Actual clicks from both opened MA at the current app route with
Bedroom Pi Plex selected. Kodi's paused position was unchanged. The interrupted
UI check recovered after starting only the existing stopped desktop-control
helper, with no local pause or pending requests present. This is navigation and
picker acceptance, not a music/book sound or speech-interruption test.
