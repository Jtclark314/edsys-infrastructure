# ESPHome in Home Assistant

## Installed state — October 16, 2026

The official stable **ESPHome Device Builder 2026.9.1** app
(`5c53de3b_esphome`, repository `https://github.com/esphome/home-assistant-addon`)
is installed and running on HAOS VM300 on pve-node3. The sidebar **ESPHome Builder**
opens `/app/5c53de3b_esphome` through HA's authenticated ingress. Boot is automatic,
watchdog enabled and automatic app updates disabled. The native app options in
`app-options.example.json` were saved and read back exactly before starting it.
The compiler defaults to two processes; per-project YAML can override this, so
it is not a hard CPU quota.

The public 6052 mapping stays disabled and `leave_front_door_open` is false.
The official app uses host networking for discovery but limits its dynamically
assigned ingress listener to local/internal interfaces and trusted ingress
peers. No standalone public dashboard or new HA credentials were added. Keep
HA admin access and app configuration private.

HA's **ESPHome integration is built in and added per device**. There are zero
ESPHome device entries and zero node YAML files at this checkpoint. No dummy
integration entry, device discovery claim, firmware compile, serial probe, USB
flash or OTA operation was performed. The actual board and sensor details are
needed before making firmware.

Verification includes native running/options/sidebar readback, successful
ESPHome backend HTML and its UI marker, the existing native ESPHome component,
and the private configuration directory. HA Core and all three pre-existing
apps retained their exact running process/start state. Active voice component
0.6.0 was retained; 0.6.1 stays staged. No HA/Pi/Nimo restart or live media/voice
operation occurred. End-user browser/USB/device acceptance remains separate.

## Ownership and programming tools

HA owns runtime device integration and automations. Each ESP32 communicates
directly with HA over its encrypted native API; the Pi is not a sensor gateway.
No MQTT broker or new automation framework is needed for ordinary sensors.

The [isolated 9950x workbench](../esphome-workbench/README.md) supplies pinned
ESPHome, Device Builder, esptool and a separate PlatformIO Core for future
Arduino/ESP-IDF projects. These tools do not install board SDKs or start a server
merely by being installed. HA-to-9950x automatic build offload is not paired or
configured; explicit local CLI builds are available when a project is selected.

Nimo is the selected first-USB-flash computer. HAOS USB passthrough and a new
Nimo IDE are unnecessary for the supported browser flashing route.

## Install or recover the app

Use HA Settings → Apps → App store → official **ESPHome Device Builder**, stable
channel. Install it only if absent, then set the options from the example using
HA's native app configuration, enable its sidebar entry and start that app.
The native CLI equivalent for an initial install is:

```sh
ha apps info 5c53de3b_esphome
ha apps install 5c53de3b_esphome
# Configure/read back the native options before the first start.
ha apps start 5c53de3b_esphome
```

Do not replace HA's storage files or expose a dashboard with authentication
bypassed. Firmware/tool versions should be reviewed together before updates.

## First device tomorrow

1. Identify the exact ESP32 board/variant/flash size, sensor model, pin map,
   voltage and power needs. Create one room-named prototype first, without
   copying guessed pins or another board's configuration.
2. Create its YAML in Builder or the private 9950x workspace. Use unique device
   names, a per-device native API encryption key, and private `secrets.yaml` for
   Wi-Fi and credentials. Leave HA-action permission off for sensor-only nodes.
3. Compile the selected project. For Nimo's first flash, download the matching
   **factory/merged** image privately on Nimo. Open
   [ESPHome Web](https://web.esphome.io/) in Chrome/Edge, connect that physical
   board with a data USB cable and choose the correct serial port/image. Local
   HTTP HA does not supply WebSerial's secure context; the HTTPS flashing page
   does. An OTA-only image is not a substitute for the initial factory image.
4. After the board joins the network, add the discovered device in HA's native
   ESPHome integration, or enter its reviewed IP/6053 and encryption key. Assign
   the area and verify real sensor updates, units, availability and reconnect.
5. Qualify sitting-still, empty-room and adjacent-room detection where relevant,
   plus sensor plausibility, before replicating the design. Test OTA only after
   first USB recovery and the selected device are established. No bulk automatic
   update or sensor-driven automation is configured by this setup.

## State, ports and backup

- Native app configuration is private Supervisor-managed state; node YAML,
  secrets and generated files live under HA `/config/esphome`.
- HA → device native API uses TCP6053. The standard ESP32 OTA endpoint is
  TCP3232 on the device. Discovery uses mDNS; reviewed manual IP setup remains
  available when discovery is unavailable.
- The 9950x private project root is
  `/home/jeremy/.local/share/edsys-esp32-tools/projects`. Its source, secrets and
  desired firmware exports are selected by the existing encrypted Restic job;
  scoped `.esphome`/`.pio` build caches are excluded. Venvs/toolchains/caches are
  reconstructible from this repository's pinned locks and are not selected.
- Native HA backup config was freshly read back: daily 01:00 America/New_York,
  seven copies, protected local agent, database and **all installed apps**
  included. The next native backup should therefore include Builder as the
  fourth app; confirm its actual metadata after completion.
- Existing completed native/export/offsite evidence predates Builder and covers
  the original three apps. The exporter checks a three-app minimum; generic
  success does not establish ESPHome coverage. No new completed four-app archive,
  project snapshot, offsite copy or ESP32 restore is claimed by this task.

Keep private firmware/configurations, Wi-Fi, API/OTA keys, pairing state and raw
logs out of Git/RAG. Restore node configuration/secrets and app options from the
matching protected backup before rebuilding. Do not overwrite newer device
identity or viewing state with a whole HA VM rollback. If removing Builder,
preserve its project directory first; removing the programming app is distinct
from removing devices already paired with native HA.

Official references: [HA ESPHome integration](https://www.home-assistant.io/integrations/esphome/),
[ESPHome setup](https://esphome.io/install/getting-started/),
[official app](https://github.com/esphome/home-assistant-addon),
[browser flashing](https://esphome.github.io/esp-web-tools/).
