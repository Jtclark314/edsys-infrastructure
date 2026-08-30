#!/usr/bin/env bash

set -Eeuo pipefail

if ((EUID != 0)); then
  echo "Run this installer as root on EdCore." >&2
  exit 1
fi

if [[ "$(hostname -s)" != "edcore-workhorse" ]]; then
  echo "Refusing to configure the wrong host: $(hostname -s)" >&2
  exit 1
fi

domain="metasploitable2-lab"
network="security-lab"
target_address="192.168.77.20"
target_mac="52:54:00:ed:77:20"
archive_name="metasploitable-linux-2.0.0.zip"
official_page="https://sourceforge.net/projects/metasploitable/files/Metasploitable2/"
official_download="https://sourceforge.net/projects/metasploitable/files/Metasploitable2/${archive_name}/download"
expected_sha256="2ae8788e95273eee87bd379a250d86ec52f286fa7fe84773a3a8f6524085a1ff"
expected_size="865084584"
download_dir="/var/lib/libvirt/boot/metasploitable2"
archive_file="${download_dir}/${archive_name}"
partial_archive="${archive_file}.part"
disk_file="/var/lib/libvirt/images/${domain}.qcow2"
runtime_root="/var/lib/edsys-control/metasploitable2-lab"
domain_xml="${runtime_root}/${domain}.xml"

for command_name in curl python3 qemu-img sha256sum unzip virsh virt-install; do
  command -v "$command_name" >/dev/null || {
    echo "Missing required host command: $command_name" >&2
    exit 1
  }
done

if virsh dominfo "$domain" >/dev/null 2>&1; then
  echo "Refusing to overwrite the existing ${domain} domain." >&2
  exit 1
fi
if [[ -e "$disk_file" ]]; then
  echo "Refusing to overwrite the existing target disk: $disk_file" >&2
  exit 1
fi

network_xml="$(virsh net-dumpxml "$network")" || {
  echo "The isolated ${network} network is not defined." >&2
  exit 1
}
python3 - "$network_xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.fromstring(sys.argv[1])
if root.find("forward") is not None:
    raise SystemExit("security-lab unexpectedly has forwarding")
dns = root.find("dns")
if dns is None or dns.get("enable") != "no":
    raise SystemExit("security-lab DNS is not disabled")
bridge = root.find("bridge")
if bridge is None or bridge.get("name") != "virbr77":
    raise SystemExit("security-lab is not on the expected isolated bridge")
ip = root.find("ip")
if ip is None or ip.get("address") != "192.168.77.1":
    raise SystemExit("security-lab has an unexpected address")
PY
[[ "$(virsh net-info "$network" | awk '/^Active:/ {print $2}')" == yes ]] || {
  echo "The isolated ${network} network is not active." >&2
  exit 1
}

available_bytes="$(df --output=avail -B1 /var/lib | tail -1 | tr -d ' ')"
if ((available_bytes < 30000000000)); then
  echo "At least 30 GB free is required before importing the target." >&2
  exit 1
fi

install -d -o root -g libvirt-qemu -m 0750 "$download_dir"
install -d -o root -g root -m 0700 "$runtime_root"

metadata_file="${runtime_root}/sourceforge-metadata.html"
curl --fail --location --retry 5 --retry-all-errors --output "$metadata_file" "$official_page"
python3 - "$metadata_file" "$archive_name" "$expected_sha256" "$official_download" <<'PY'
import json
import pathlib
import re
import sys

page = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
name, expected_hash, expected_url = sys.argv[2:]
match = re.search(r"net\.sf\.files\s*=\s*(\{.*?\});", page, re.DOTALL)
if not match:
    raise SystemExit("Could not locate SourceForge file metadata")
metadata = json.loads(match.group(1))
entry = metadata.get(name)
if not isinstance(entry, dict):
    raise SystemExit("Official SourceForge metadata did not contain the expected archive")
if entry.get("sha256") != expected_hash:
    raise SystemExit("Official SourceForge SHA-256 metadata changed")
if entry.get("download_url") != expected_url:
    raise SystemExit("Official SourceForge download URL changed")
PY
chmod 0600 "$metadata_file"

if [[ ! -e "$archive_file" ]]; then
  curl --fail --location --retry 8 --retry-all-errors --continue-at - \
    --output "$partial_archive" "$official_download"
  mv "$partial_archive" "$archive_file"
fi

actual_size="$(stat -c %s "$archive_file")"
if [[ "$actual_size" != "$expected_size" ]]; then
  echo "Metasploitable archive size verification failed." >&2
  exit 1
fi
printf '%s  %s\n' "$expected_sha256" "$archive_file" | sha256sum --check
chown root:libvirt-qemu "$archive_file"
chmod 0640 "$archive_file"

extract_root="${runtime_root}/extract"
if [[ -e "$extract_root" ]]; then
  echo "Refusing to reuse an existing extraction directory: $extract_root" >&2
  exit 1
fi
install -d -o root -g root -m 0700 "$extract_root"
python3 - "$archive_file" "$extract_root" <<'PY'
import pathlib
import shutil
import stat
import sys
import zipfile

archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive) as bundle:
    entries = bundle.infolist()
    if not entries or len(entries) > 64:
        raise SystemExit("Unexpected archive entry count")
    total_size = sum(entry.file_size for entry in entries)
    if total_size > 20 * 1024**3:
        raise SystemExit("Archive expands beyond the 20 GiB safety limit")
    for entry in entries:
        relative = pathlib.PurePosixPath(entry.filename)
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit("Unsafe path in archive")
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise SystemExit("Symlinks are not permitted in the target archive")
        output = destination.joinpath(*relative.parts)
        if not output.resolve().is_relative_to(destination):
            raise SystemExit("Archive path escaped the extraction root")
        if entry.is_dir():
            output.mkdir(parents=True, exist_ok=True)
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(entry) as source, output.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
PY

mapfile -t vmdk_files < <(find "$extract_root" -type f -name 'Metasploitable.vmdk' -print)
if ((${#vmdk_files[@]} != 1)); then
  echo "Expected exactly one Metasploitable.vmdk in the verified archive." >&2
  exit 1
fi
source_disk="${vmdk_files[0]}"
source_info="$(qemu-img info --output=json -f vmdk "$source_disk")"
python3 - "$source_info" <<'PY'
import json
import sys

info = json.loads(sys.argv[1])
if info.get("format") != "vmdk":
    raise SystemExit("Verified archive did not contain a VMDK disk")
size = info.get("virtual-size", 0)
if not 4 * 1024**3 <= size <= 20 * 1024**3:
    raise SystemExit("VMDK virtual size is outside the accepted range")
if info.get("backing-filename"):
    raise SystemExit("VMDK unexpectedly has a backing file")
PY

partial_disk="${disk_file}.part"
qemu-img convert -p -f vmdk -O qcow2 \
  -o compat=1.1,lazy_refcounts=on,cluster_size=65536 \
  "$source_disk" "$partial_disk"
qemu-img check --quiet "$partial_disk"
mv "$partial_disk" "$disk_file"
chown root:root "$disk_file"
chmod 0600 "$disk_file"

if ! virsh net-dumpxml "$network" | grep -Fq "mac address='${target_mac}'"; then
  virsh net-update "$network" add ip-dhcp-host \
    "<host mac='${target_mac}' name='${domain}' ip='${target_address}'/>" \
    --live --config >/dev/null
fi

if ! ufw status | grep -Fq '# EdSys security lab containment'; then
  ufw route deny in on virbr77 from 192.168.77.0/24 to any \
    comment 'EdSys security lab containment'
fi

virt-install \
  --connect qemu:///system \
  --name "$domain" \
  --description "EdSys deliberately vulnerable Metasploitable 2 training target" \
  --memory 2048 \
  --vcpus 2 \
  --cpu host-passthrough \
  --machine pc \
  --boot hd \
  --disk "path=${disk_file},format=qcow2,bus=ide,cache=none" \
  --network "network=${network},model=e1000,mac=${target_mac}" \
  --graphics spice,listen=127.0.0.1 \
  --video vga \
  --sound none \
  --osinfo detect=on,require=off \
  --import \
  --noautoconsole \
  --print-xml >"$domain_xml"

python3 - "$domain_xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
interfaces = root.findall("./devices/interface")
if len(interfaces) != 1:
    raise SystemExit("Generated target definition does not have exactly one interface")
source = interfaces[0].find("source")
if source is None or source.get("network") != "security-lab":
    raise SystemExit("Generated target definition is not isolated")
graphics = root.find("./devices/graphics")
if graphics is None or graphics.get("listen") != "127.0.0.1":
    raise SystemExit("Generated target graphics are not loopback-only")
PY

virsh define "$domain_xml" >/dev/null
virsh autostart "$domain" --disable >/dev/null

python3 - "$runtime_root/manifest.json" "$official_download" "$expected_sha256" "$expected_size" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
temporary = path.with_suffix(".json.tmp")
value = {
    "archive_source": sys.argv[2],
    "archive_sha256": sys.argv[3],
    "archive_size": int(sys.argv[4]),
    "domain": "metasploitable2-lab",
    "network": "security-lab",
    "role": "deliberately-vulnerable-training-target",
    "version": 1,
}
temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY

find "$extract_root" -depth -delete
echo "Metasploitable 2 target imported but not started. Run the verifier after first boot and snapshot creation."
