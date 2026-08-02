# EdCore SDR Stack

This folder is the sanitized deployment source for the isolated `edcore-sdr`
VM. OpenWebRX+ is the normal and sole USB owner. A loopback-only `rtl_tcp`
service is available on demand for desktop applications through an SSH tunnel.
The two services intentionally conflict so multiple processes cannot claim the
NESDR at the same time.

## Hardware and switching contract

- Receiver: Nooelec NESDR SMArt v5, serial `00000001`.
- Upconverter: Ham It Up with a nominal 125 MHz oscillator.
- Proxmox attaches the receiver to VMID 322 by stable physical USB path `3-1`.
- Profiles beginning `PASS-THROUGH` require the Ham It Up pass-through position.
- Profiles beginning `HAM IT UP ENABLE` require the powered upconverter path.
- The tracked HF profiles use OpenWebRX+'s `lfo_offset=125000000` formula. They
  do not enable RTL direct sampling.

The receiver-only stack does not transmit. Do not add transmitting hardware or
automated Internet reporting without a separate review. APRS-IS, PSKReporter,
WSPRnet, SondeHub, AIS reporting, and MQTT publishing are disabled by default.

## Deployment

Copy this directory to a clean Ubuntu 24.04 guest. The base installer follows
the official OpenWebRX+ Noble repository flow, verifies the public signing-key
fingerprint before changing APT state, and installs OpenWebRX+ plus the radio,
build, diagnostic, NFS, guest-agent, and Cockpit package baseline. Then install
the commit/version-pinned optional decoders and apply the EdSys configuration:

```bash
sudo ./install-base-packages.sh --apply
sudo ./install-aprs-symbols.sh
sudo ./install-dump1090.sh
sudo ./install-freedv-rx.sh
sudo ./install-m17-demod.sh
sudo ./install-satdump.sh
sudo ./install-edcore-sdr.sh --apply
```

The optional decoder installers are version/commit pinned. SatDump's official
Ubuntu 24.04 package is checked against a recorded SHA-256 before installation.
The FreeDV command-line decoder is built from the stable Codec2 1.2.0 source;
the Ubuntu `freedv` desktop package alone does not contain `freedv_rx`.
SoftMBE is deliberately absent; upstream OpenWebRX+ warns about its provenance.
The accepted 2026-08-02 guest runs OpenWebRX+ 1.2.119, SatDump 1.2.2,
FlightAware dump1090 commit `74f9e6c4b0efe35c27e6806f8f0d9bbe49b8a6b1`,
M17 demod commit `9b8cec24d3f8d5e9f7f6e9c23661439e32343d6b`, and
Codec2/FreeDV commit `06d4c11e699b0351765f10398abb4f663a984f36`.

Create or reset the private OpenWebRX administrator separately, supplying the
password through `OWRX_PASSWORD`. Never put that value in this repository.

## Control and access

Normal browser service:

```bash
sudo edsys-sdrctl web
```

On-demand raw network mode:

```bash
sudo edsys-sdrctl rtl-tcp
ssh -N -L 1234:127.0.0.1:1234 edcore-sdr
```

Point SDR++, GQRX, or another client to `rtl_tcp://127.0.0.1:1234`. Port 1234
never listens on a LAN address. Return the receiver to normal mode with
`sudo edsys-sdrctl web`.

For one bounded exclusive CLI job:

```bash
sudo edsys-sdrctl run -- timeout 60 rtl_power -f 88M:108M:100k /srv/edsys-sdr-data/spectrum/fm.csv
```

## Storage, backup, and restore

`192.168.50.50:/mnt/ai-store/edsys-sdr` automounts at
`/srv/edsys-sdr-data`. It is exported only to `192.168.50.80`, uses matching
GID 3220, maps every identity from this dedicated guest to the unprivileged
storage owner with NFS `all_squash`, and contains separate raw, recordings,
decoded, spectrum, satellite, curated, configuration-backup, and metadata
areas. OpenWebRX temporary files remain local so an NFS outage does not stop
the receiver.

The hourly sync copies only OpenWebRX settings, bookmarks, and the private user
hash file into `config-backups/current`. Large raw IQ and routine recordings
must be excluded from encrypted offsite backup unless explicitly curated.

To restore, reinstall the package and pinned helpers, run the main installer,
restore the private `users.json` and reviewed settings/bookmarks, then restart
OpenWebRX and run `sudo verify-edcore-sdr`.

## Verification

```bash
python3 -m unittest discover -s tests -v
sudo ./install-base-packages.sh --check
sudo verify-edcore-sdr
sudo edsys-sdrctl status
```

The first live RF smoke test should use a known local FM broadcast while the
Ham It Up is in pass-through. HF acceptance remains incomplete until an HF
antenna is deployed and the upconverter is placed in its enabled path.
