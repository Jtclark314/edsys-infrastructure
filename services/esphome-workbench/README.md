# ESP32 programming workbench on 9950x

This is the isolated management/build toolkit for future ESPHome, Arduino and
ESP-IDF projects. Hardware-specific projects, board selection and firmware builds
remain separate decisions. The first USB flash is planned on **Nimo**, using a
board-matched factory/merged `.bin` downloaded there and
[ESPHome Web](https://web.esphome.io/) in Chrome/Edge over HTTPS. Do not install
Nimo serial drivers until the actual board/USB bridge is identified.

## Pinned tools

| Unique command | Installed capability | Environment |
| --- | --- | --- |
| `edsys-esphome` | ESPHome 2026.9.1 | ESPHome venv |
| `edsys-espbuilder` | Device Builder 1.14.9, frontend 0.1.329 | ESPHome venv |
| `edsys-esptool` | esptool 5.3.1; pyserial 3.5 available for future serial workflows | ESPHome venv |
| `edsys-pio` | PlatformIO Core 6.2.0, latest stable verified 2026-10-16 | Separate Core venv |

ESPHome 2026.9.1 requires Python >=3.12,<3.15 and pins its own PlatformIO
**6.1.19**. Its environment intentionally retains that version. Standalone Core
6.2.0 supports future Arduino/ESP-IDF projects without replacing ESPHome's Core.
Both full dependency closures are version-pinned and distribution-hashed in
`requirements-*.lock`; no prereleases or development/test extras are requested.
The ESPHome closure includes Device Builder explicitly rather than adding an
unbounded package to an existing environment.

Primary sources: [ESPHome CLI](https://esphome.io/guides/cli/),
[ESPHome 2026.9.1 package](https://pypi.org/project/esphome/2026.9.1/),
[Device Builder 1.14.9](https://pypi.org/project/esphome-device-builder/1.14.9/),
[PlatformIO Core 6.2.0](https://pypi.org/project/platformio/6.2.0/),
[Core installation](https://docs.platformio.org/en/latest/core/installation/index.html),
[Core environment variables](https://docs.platformio.org/en/latest/envvars.html).

## Install and layout

Run explicitly as the intended user, without sudo or activating another venv:

```sh
/usr/bin/python3 services/esphome-workbench/install.py
```

Prerequisites are the existing `/usr/bin/python3` (9950x: 3.12.3) and managed `uv`
(installation verified with 0.11.28). The installer uses PyPI with the checked-in
hashes. It creates only these paths:

- `~/.local/share/edsys-esp32-tools/` (0700 owned runtime root).
- `venvs/<tool>-<version>-<lock-hash-prefix>/`: separate, versioned Python envs.
- `projects/`: private YAML, `secrets.yaml`, API/encryption keys, Wi-Fi details,
  firmware, and project build artifacts belong here, outside Git.
- `state/esphome-platformio/` and `state/platformio/`: separate PlatformIO Core
  roots, so the default `~/.platformio` is not used by these launchers.
- `cache/`: uv and separately scoped PlatformIO caches.
- `bin/`, `install.json`, `verification.json`: launchers and private provenance.
- `~/.local/bin/edsys-esphome`, `edsys-espbuilder`, `edsys-esptool`, `edsys-pio`:
  unique symlinks into the private runtime. No shell startup files are edited.

An existing unmarked runtime or unrelated launcher collision causes refusal.
Identical lock hashes reuse an existing environment only after its complete
canonicalized package/version map matches the lock exactly and dependency
compatibility passes. Transitive version drift, missing or extra packages are
refused; changed locks produce a new environment. If installation fails
before its environment stamp is written, inspect that exact incomplete directory
and remove only it before retrying. The installer never adopts arbitrary files.
Runtime installer/launcher/lock hashes and all installed distribution versions
are recorded in `install.json`. Package-source wheel construction may use the
local C compiler for Python dependencies; no ESP32 compiler, board platform,
Arduino framework or ESP-IDF SDK is installed here yet.

The launchers use umask 0077, isolated Python imports, private Core/cache paths,
disabled Core telemetry/upgrade checks, and two compile jobs through
`ESPHOME_DEFAULT_COMPILE_PROCESS_LIMIT=2` and `PLATFORMIO_RUN_JOBS=2`.
Use `edsys-pio run -j 2` explicitly for a future authorized project build.
A project-level ESPHome `compile_process_limit` can override its default; retain
a limit of two. The Python installer also caps package-build concurrency at two.

No service, port mapping, udev rule, group change, background daemon, serial
connection, discovery scan or device pairing is installed or started.
`edsys-espbuilder` defaults to loopback (`--host 127.0.0.1`); merely installing it
starts no listener. An explicit operator `--host` can override that default.
Automatic HA-to-9950x build offload is **not paired or configured**. Starting the
manual dashboard or remote builder is a separate runtime action; use
`edsys-espbuilder --help` to review options without starting it.

## Local verification and current limits

```sh
python3 -m unittest discover -s services/esphome-workbench -p 'test_*.py' -v
/usr/bin/python3 services/esphome-workbench/verify_offline.py
edsys-esphome version
edsys-pio --version
edsys-esptool version
edsys-espbuilder --version
```

The offline verifier copies the fixed credential-free `examples/schema-only.yaml`
to the private `projects/schema-only/` directory. It validates a **host-platform
schema fixture**, not an ESP32 board configuration, and confirms the two-job
default. It does not compile or run the host fixture. A Python audit guard refuses
network/device access and subprocess discovery; only local `xdg-user-dir DOCUMENTS`
and `uname -p` metadata reads are permitted. The dependency's transient IPv6
loopback availability probe is blocked with its normal unavailable fallback.
The guard is a regression check for trusted CLIs, not a security sandbox.
Only successful checks write `verification.json`; raw expanded YAML is not saved.

On 2026-10-16, CLI versions, both dependency compatibility checks, the offline
schema validation and installer repeat invocation passed. There were 101 ESPHome
and 22 standalone Core distributions. Both owned Core roots had no installed
board platforms/packages. No firmware has been built, flashed, tested on a board,
or registered with HA. No driver, serial-port, Wi-Fi, OTA or HA device acceptance
is implied. Existing voice/media/display test holds remain in effect.

For future work, identify the exact board/module, flash size, USB bridge,
peripherals/pin map and power needs first. Keep real credentials in the private
workspace and select the correct factory/merged image for the initial Nimo USB
flash. ESPHome `run` performs compile/upload/logging, and esptool/PlatformIO also
have commands that operate real devices; use them only within that device's
authorized commissioning task. Hardware support/SDK downloads occur with a
future explicitly chosen project, not with this installation.

## Recovery and backup

Source plus the hashed locks reconstruct the Python tools using a compatible
system Python. Before switching tool revisions, retain the previous private
`install.json` and its referenced versioned venv directories; restore that
manifest and matching launcher files to select the prior pair. The installer
does not garbage-collect older environments.

Back up private project YAML, secrets, any future Device Builder pairing/auth
state, and desired firmware exports separately with appropriate access control.
Do not commit or publish them. Cache/venv reconstruction is not a restore of
project credentials or devices. No recurring backup enrollment is established
by this installer. To uninstall, remove only the four symlinks after confirming
their exact targets; preserve private projects before removing the owned tools
root. No service needs to be stopped because none is installed.
