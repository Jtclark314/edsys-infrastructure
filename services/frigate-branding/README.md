# Frigate browser icon

Jeremy selected an orange plasma circle that fills nearly the entire icon on
2026-10-09, replacing the earlier metallic blue badge to improve visibility at
small sidebar sizes. `assets/selected-master.png` is the approved high-resolution
design; PNG sizes and a multi-resolution ICO are derived from that file.

## Deployment

The live Frigate stack remains owned by `/opt/stacks/frigate/docker-compose.yml`.
Copy this folder to `/opt/frigate-branding`, merge `compose.fragment.yaml` into
the existing service, validate Compose, and recreate only Frigate. Retain the
existing image, environment, network, GPU, camera/configuration and media mounts.
No new ports, accounts or network routes are needed.

The read-only `/opt/frigate-branding` mount contains only the icon assets and
startup helper. Before invoking Frigate's normal `/init`, the helper updates the
upstream frontend's icon links and copies the icons into its disposable web
directory. It reads the upstream web manifest and preserves its other settings.
Icon URLs contain a content fingerprint so an old browser asset cache does not
reuse the previous site icon. The helper runs on every start, including a
Compose recreation using a newer upstream image; it discovers current manifest
filenames rather than pinning the old compiled frontend filenames.

An unexpected frontend layout logs an icon warning and continues normal Frigate
startup. The branding must never prevent camera recording. Recheck icon links
after upstream upgrades; compatibility with an unknown future layout is not
guaranteed.

For a later artwork-only revision, back up `/opt/frigate-branding`, replace its
assets with the newly derived set, and run the mounted helper without `--start`
inside the existing container. Validate NGINX configuration and gracefully
reload NGINX so its cached index descriptor is refreshed. This updates the
current frontend without restarting camera processing; the existing startup
hook applies the same assets on future starts.

## Backup and rollback

Before the first deployment, retain the previous Compose file and upstream icon
files in a root-private timestamped folder under `/var/backups/frigate-branding`.
Keep private Compose contents and recovery files outside Git and RAG. The custom
source assets and helper are tracked here; runtime copies are under
`/opt/frigate-branding`. Include that runtime folder with stack configuration
backups.

To restore a previous custom design, copy the asset set from the corresponding
private `runtime-before/assets` backup into `/opt/frigate-branding/assets`, run
the helper without `--start`, and validate/reload NGINX. The prior tracked source
revision also retains that artwork. Match the served icon bytes after rollback.

To restore the upstream icon, restore the pre-change Compose file (or remove
only the custom entrypoint and icon mount) and recreate Frigate from its existing
upstream image. The image supplies the original frontend. Check camera recovery
and browser icon links afterward. A rollback/recreation briefly interrupts
camera processing.

## Verification

- Validate the live Compose file with `docker compose ... config --quiet`.
- Exercise the helper in a network-isolated disposable container using the
  same upstream image; verify repeated application leaves one icon set and
  preserves frontend module scripts.
- Fetch Frigate's HTML, each referenced icon and all web-manifest icons. Compare
  returned bytes with the selected assets; verify `/favicon.ico` as well.
- After deployment, require Docker health plus recovered camera/process FPS,
  zero skipped frames and changing latest-frame hashes for all seven cameras.
- Open the real Frigate page in a browser and check that the frontend loads.
  Existing pinned/sidebar entries can retain a separate browser icon cache;
  their appearance must be checked on the relevant device. Reload the entry
  first, and re-add it only if necessary. Do not clear the entire browser profile.

First acceptance on 2026-10-08 used Frigate `0.17.2-3d4dd3a`. The revised plasma
circle was checked at 16, 24, 32, 48 and 64 pixels against a dark background on
2026-10-09.
