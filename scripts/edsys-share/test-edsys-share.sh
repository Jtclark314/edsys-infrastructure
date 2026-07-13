#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

scripts=(
  render-samba-config.py
  edsys-share-mount-check
  edsys-share-tailnet-guard
  edsys-share-gdrive-sync
  edsys-share-gdrive-verify
  edsys-share-gdrive-prune
  edsys-share-gdrive-restore
  install-9950x.sh
  install-rclone-1.74.4.sh
)
for script in "${scripts[@]}"; do
  [[ -x "${SCRIPT_DIR}/${script}" ]] || { echo "Script is not executable: ${script}" >&2; exit 1; }
  if [[ "${script}" == *.py ]]; then
    python3 - "${SCRIPT_DIR}/${script}" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
compile(path.read_text(), str(path), "exec")
PY
  else
    bash -n "${SCRIPT_DIR}/${script}"
  fi
done
for source_file in "${SCRIPT_DIR}/README.md" "${SCRIPT_DIR}/edsys-share.conf.example" "${SCRIPT_DIR}/samba-share.conf" "${SCRIPT_DIR}"/systemd/*; do
  [[ ! -x "${source_file}" ]] || { echo "Data/unit source is unexpectedly executable: ${source_file}" >&2; exit 1; }
done
cc -std=c11 -O2 -Wall -Wextra -Werror "${SCRIPT_DIR}/edsys-share-mount-check-smb.c" -o "${tmp}/edsys-share-mount-check-smb"
"${tmp}/edsys-share-mount-check-smb"
if command -v shellcheck >/dev/null; then
  for script in "${scripts[@]}"; do
    [[ "${script}" == *.py ]] || shellcheck "${SCRIPT_DIR}/${script}"
  done
fi

cat >"${tmp}/smb.conf" <<EOF
[global]
   inter faces = docker0
   bindinterfacesonly = no
   hostsallow = 0.0.0.0/0
   hostsdeny = 127.0.0.1
   configbackend = registry
   registryshares = yes
   usersharemaxshares = 100
   default = EdSys-Share
   preload = EdSys-Share
   loadprinters = yes
   serverrole = member server
   security = ads
   passdbbackend = ldapsam
   usernamemap = /tmp/unsafe-users.map
   usernamemapscript = /tmp/unsafe-user-map-script
   rootdir = /tmp/unsafe-chroot
   smbports = 139
   server addresses = 203.0.113.1
   msdfs root = yes
   msdfs proxy = \\untrusted.example\capture
   magic script = run-me.sh
   magic output = run-me.out

[printers]
   path = /tmp/printers
   printable = yes

[print$]
   path = /tmp/print-drivers

[EdSysVault]
   path = /tmp/vault
   force user = jeremy

[Unrelated]
   path = /tmp/unrelated
   comment = preserve-me
   invalidusers = alice

[Quoted]
   path = /tmp/quoted
   invalid  users = "Jane Doe"

[SingleQuoted]
   path = /tmp/single-quoted
   invalid users = 'edsys-share-dell'

[Backslash]
   path = /tmp/backslash
   invalid users = DOMAIN\edsys-share-dell

[PlainEarlier]
   path = /tmp/plain-earlier
   invalid users = edsys-share-dell alice

[EdSys-Share]
   path = /tmp/stale-one
   admin users = root

[eDsYs-ShArE]
   path = /tmp/stale-two
   write list = root
EOF
"${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/smb.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/rendered-smb.conf" \
  --restricted-user edsys-share-dell
testparm -s "${tmp}/rendered-smb.conf" >/dev/null
testparm -sv "${tmp}/rendered-smb.conf" 2>/dev/null >"${tmp}/effective-smb.conf"
grep -Fq 'interfaces = lo enp7s0' "${tmp}/rendered-smb.conf"
grep -Fq 'config backend = file' "${tmp}/rendered-smb.conf"
grep -Fq 'registry shares = no' "${tmp}/rendered-smb.conf"
grep -Fq 'usershare max shares = 0' "${tmp}/rendered-smb.conf"
grep -Fq 'default service =' "${tmp}/rendered-smb.conf"
grep -Fq 'auto services =' "${tmp}/rendered-smb.conf"
grep -Fq 'load printers = no' "${tmp}/rendered-smb.conf"
grep -Fq 'server role = standalone server' "${tmp}/rendered-smb.conf"
grep -Fq 'security = user' "${tmp}/rendered-smb.conf"
grep -Fq 'passdb backend = tdbsam' "${tmp}/rendered-smb.conf"
grep -Fq 'username map =' "${tmp}/rendered-smb.conf"
grep -Fq 'username map script =' "${tmp}/rendered-smb.conf"
grep -Fq 'root directory =' "${tmp}/rendered-smb.conf"
grep -Fq 'smb ports = 445' "${tmp}/rendered-smb.conf"
grep -Fq 'valid users = jeremy edsys-share-dell' "${tmp}/rendered-smb.conf"
grep -Fq 'force user = jeremy' "${tmp}/rendered-smb.conf"
grep -Fq 'available = yes' "${tmp}/rendered-smb.conf"
grep -Fq 'browseable = yes' "${tmp}/rendered-smb.conf"
grep -Fq 'printable = no' "${tmp}/rendered-smb.conf"
grep -Fq 'max connections = 0' "${tmp}/rendered-smb.conf"
grep -Fq 'comment = preserve-me' "${tmp}/rendered-smb.conf"
grep -Fq 'invalid users = alice edsys-share-dell' "${tmp}/rendered-smb.conf"
grep -Fq 'invalid users = "Jane Doe" edsys-share-dell' "${tmp}/rendered-smb.conf"
grep -Fq "invalid users = 'edsys-share-dell' edsys-share-dell" "${tmp}/rendered-smb.conf"
grep -Fq 'invalid users = DOMAIN\edsys-share-dell edsys-share-dell' "${tmp}/rendered-smb.conf"
grep -Fq 'invalid users = edsys-share-dell alice edsys-share-dell' "${tmp}/rendered-smb.conf"
if grep -Eqi '^\s*(inter faces|bindinterfacesonly|hostsallow|hostsdeny|configbackend|registryshares|usersharemaxshares|default|preload|loadprinters|serverrole|passdbbackend|usernamemap|usernamemapscript|rootdir|smbports|invalidusers)\s*=' "${tmp}/rendered-smb.conf"; then
  echo "Renderer preserved a whitespace-normalized Samba option alias" >&2
  exit 1
fi
if grep -Fq 'admin users = root' "${tmp}/rendered-smb.conf"; then
  echo "Renderer preserved stale managed admin users" >&2
  exit 1
fi
if grep -Fq 'write list = root' "${tmp}/rendered-smb.conf"; then
  echo "Renderer preserved stale managed write list" >&2
  exit 1
fi
python3 - "${tmp}/rendered-smb.conf" <<'PY'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text()
sections = re.findall(
    r"(?ms)^\[([^\n\]]+)\][ \t]*\n(.*?)(?=^\[[^\n\]]+\][ \t]*\n|\Z)",
    text,
)
names = [name.casefold() for name, _ in sections]
assert names.count("edsys-share") == 1
for name, body in sections:
    if name.casefold() not in {"global", "edsys-share"}:
        assert re.search(
            r"(?mi)^\s*invalid users\s*=.*\bedsys-share-dell\b", body
        ), name
PY
python3 - "${tmp}/effective-smb.conf" <<'PY'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text()
assert re.search(r"(?mi)^\s*registry shares\s*=\s*No\s*$", text)
assert re.search(r"(?mi)^\s*usershare max shares\s*=\s*0\s*$", text)
assert re.search(r"(?mi)^\s*config backend\s*=\s*file\s*$", text)
assert re.search(r"(?mi)^\s*default service\s*=\s*$", text)
assert re.search(r"(?mi)^\s*auto services\s*=\s*$", text)
assert re.search(r"(?mi)^\s*load printers\s*=\s*No\s*$", text)
assert re.search(r"(?mi)^\s*server role\s*=\s*standalone server\s*$", text)
assert re.search(r"(?mi)^\s*security\s*=\s*USER\s*$", text)
assert re.search(r"(?mi)^\s*passdb backend\s*=\s*tdbsam\s*$", text)
assert re.search(r"(?mi)^\s*username map\s*=\s*$", text)
assert re.search(r"(?mi)^\s*username map script\s*=\s*$", text)
assert re.search(r"(?mi)^\s*root directory\s*=\s*$", text)
assert re.search(r"(?mi)^\s*smb ports\s*=\s*445\s*$", text)
sections = re.findall(
    r"(?ms)^\[([^\n\]]+)\][ \t]*\n(.*?)(?=^\[[^\n\]]+\][ \t]*\n|\Z)",
    text,
)
for name, body in sections:
    if name.casefold() not in {"global", "edsys-share"}:
        assert re.search(
            r"(?mi)^\s*invalid users\s*=.*\bedsys-share-dell\b", body
        ), name
PY

"${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/rendered-smb.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/rendered-twice.conf" \
  --restricted-user edsys-share-dell
cmp -s "${tmp}/rendered-smb.conf" "${tmp}/rendered-twice.conf"

cat >"${tmp}/misplaced-interfaces.conf" <<EOF
[global]
   bind interfaces only = yes

[Unrelated]
   path = /tmp/unrelated
   inter faces = docker0
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/misplaced-interfaces.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/should-not-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted interfaces outside [global]" >&2
  exit 1
fi

cat >"${tmp}/included.conf" <<EOF
[global]
   in clude = /tmp/extra-samba.conf
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/included.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/included-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted an uninspected Samba include" >&2
  exit 1
fi

cat >"${tmp}/alternate-config.conf" <<EOF
[global]
   configfile = /tmp/per-client-%I.conf
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/alternate-config.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/alternate-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted an alternate Samba config file" >&2
  exit 1
fi

cat >"${tmp}/duplicate-deny.conf" <<EOF
[global]

[Unrelated]
   path = /tmp/unrelated
   invalidusers = edsys-share-dell
   invalid users = alice
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/duplicate-deny.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/duplicate-deny-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted ambiguous duplicate invalid-users options" >&2
  exit 1
fi

cat >"${tmp}/continued-option.conf" <<'EOF'
[global]
   config \
   file = /tmp/per-client-%I.conf
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/continued-option.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/continued-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted a continued Samba parameter name" >&2
  exit 1
fi

cat >"${tmp}/globals-alias.conf" <<EOF
[global]
   workgroup = WORKGROUP

[globals]
   workgroup = WORKGROUP
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/globals-alias.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/globals-alias-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted the Samba [globals] alias" >&2
  exit 1
fi

for spaced_global in 'g l o b a l' 'glo bal' 'g l o b a l s'; do
  cat >"${tmp}/spaced-global-alias.conf" <<EOF
[global]
   workgroup = WORKGROUP

[${spaced_global}]
   workgroup = WORKGROUP
EOF
  if "${SCRIPT_DIR}/render-samba-config.py" \
    "${tmp}/spaced-global-alias.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/spaced-global-render.conf" \
    --restricted-user edsys-share-dell 2>/dev/null; then
    echo "Renderer accepted Samba special global alias [${spaced_global}]" >&2
    exit 1
  fi
done

cat >"${tmp}/spaced-service-alias.conf" <<EOF
[global]

[Foo Bar]
   path = /tmp/foo-bar

[FooBar]
   path = /tmp/foobar
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/spaced-service-alias.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/spaced-service-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted whitespace-equivalent Samba service names" >&2
  exit 1
fi

cat >"${tmp}/spaced-managed-share.conf" <<EOF
[global]

[Ed Sys-Share]
   path = /tmp/lookalike
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/spaced-managed-share.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/spaced-managed-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted a whitespace alias of the managed share" >&2
  exit 1
fi

cat >"${tmp}/global-inheritance.conf" <<EOF
[global]
   adminusers = edsys-share-dell
   forcegroup = root
   readlist = edsys-share-dell
EOF
"${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/global-inheritance.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/global-inheritance-render.conf" \
  --restricted-user edsys-share-dell
for reset_option in 'admin users' 'force group' 'read list'; do
  [[ -z "$(testparm -s --section-name=EdSys-Share --parameter-name="${reset_option}" "${tmp}/global-inheritance-render.conf" 2>/dev/null)" ]]
done

cat >"${tmp}/global-synonym.conf" <<EOF
[global]
   allowhosts = 0.0.0.0/0
   denyhosts = 127.0.0.1
EOF
"${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/global-synonym.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/global-synonym-render.conf" \
  --restricted-user edsys-share-dell
[[ "$(testparm -s --section-name=EdSys-Share --parameter-name='hosts allow' "${tmp}/global-synonym-render.conf" 2>/dev/null)" == '127.0.0.1 192.168.50.0/24' ]]
[[ "$(testparm -s --section-name=EdSys-Share --parameter-name='hosts deny' "${tmp}/global-synonym-render.conf" 2>/dev/null)" == '0.0.0.0/0' ]]

for unsafe_hosts_option in \
  'allow hosts = 0.0.0.0/0' \
  'deny hosts = 127.0.0.1'; do
  cat "${SCRIPT_DIR}/samba-share.conf" >"${tmp}/unsafe-hosts-fragment.conf"
  printf '   %s\n' "${unsafe_hosts_option}" >>"${tmp}/unsafe-hosts-fragment.conf"
  if "${SCRIPT_DIR}/render-samba-config.py" \
    "${tmp}/smb.conf" "${tmp}/unsafe-hosts-fragment.conf" "${tmp}/unsafe-hosts-render.conf" \
    --restricted-user edsys-share-dell 2>/dev/null; then
    echo "Renderer accepted share-local hosts override: ${unsafe_hosts_option}" >&2
    exit 1
  fi
done

for unsafe_target_option in \
  'msdfs root = yes' \
  'msdfs proxy = \\untrusted.example\capture' \
  'magic script = run-me.sh' \
  'server addresses = 203.0.113.1' \
  'hide files = /*/'; do
  cat "${SCRIPT_DIR}/samba-share.conf" >"${tmp}/unsafe-target-fragment.conf"
  printf '   %s\n' "${unsafe_target_option}" >>"${tmp}/unsafe-target-fragment.conf"
  if "${SCRIPT_DIR}/render-samba-config.py" \
    "${tmp}/smb.conf" "${tmp}/unsafe-target-fragment.conf" "${tmp}/unsafe-target-render.conf" \
    --restricted-user edsys-share-dell 2>/dev/null; then
    echo "Renderer accepted unsafe target option: ${unsafe_target_option}" >&2
    exit 1
  fi
done

cat >"${tmp}/unsafe-fragment.conf" <<EOF
$(cat "${SCRIPT_DIR}/samba-share.conf")

[Accidental-Second-Share]
   path = /tmp/accidental
   validusers = edsys-share-dell
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/smb.conf" "${tmp}/unsafe-fragment.conf" "${tmp}/unsafe-fragment-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted an extra service in the managed share fragment" >&2
  exit 1
fi

cat >"${tmp}/unsafe-prefix-fragment.conf" <<EOF
invalidusers = edsys-share-dell
$(cat "${SCRIPT_DIR}/samba-share.conf")
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/smb.conf" "${tmp}/unsafe-prefix-fragment.conf" "${tmp}/unsafe-prefix-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted active content before the fragment section" >&2
  exit 1
fi

sed 's/valid users = jeremy edsys-share-dell/valid users = jeremy edsys-share-dell root/' \
  "${SCRIPT_DIR}/samba-share.conf" >"${tmp}/unsafe-valid-users-fragment.conf"
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/smb.conf" "${tmp}/unsafe-valid-users-fragment.conf" "${tmp}/unsafe-users-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted an extra EdSys-Share valid user" >&2
  exit 1
fi

sed "s/valid users = jeremy edsys-share-dell/valid users = 'jeremy' edsys-share-dell/" \
  "${SCRIPT_DIR}/samba-share.conf" >"${tmp}/quoted-valid-users-fragment.conf"
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/smb.conf" "${tmp}/quoted-valid-users-fragment.conf" "${tmp}/quoted-users-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted quoted lookalike EdSys-Share valid users" >&2
  exit 1
fi

cat "${SCRIPT_DIR}/samba-share.conf" >"${tmp}/unsafe-admin-fragment.conf"
printf '%s\n' '   adminusers = edsys-share-dell' >>"${tmp}/unsafe-admin-fragment.conf"
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/smb.conf" "${tmp}/unsafe-admin-fragment.conf" "${tmp}/unsafe-admin-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted an EdSys-Share admin user" >&2
  exit 1
fi

cat "${SCRIPT_DIR}/samba-share.conf" >"${tmp}/unsafe-writable-fragment.conf"
printf '%s\n' '   writable = no' >>"${tmp}/unsafe-writable-fragment.conf"
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/smb.conf" "${tmp}/unsafe-writable-fragment.conf" "${tmp}/unsafe-writable-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted an effective read-only synonym override" >&2
  exit 1
fi

cat >"${tmp}/missing-global.conf" <<EOF
[Unrelated]
   path = /tmp/unrelated
EOF
if "${SCRIPT_DIR}/render-samba-config.py" \
  "${tmp}/missing-global.conf" "${SCRIPT_DIR}/samba-share.conf" "${tmp}/missing-global-render.conf" \
  --restricted-user edsys-share-dell 2>/dev/null; then
  echo "Renderer accepted a configuration without [global]" >&2
  exit 1
fi

cp "${SCRIPT_DIR}"/systemd/*.service "${SCRIPT_DIR}"/systemd/*.timer "${tmp}/"
sed 's/@TAILNET_LISTEN_IP@/100.87.137.47/g' "${SCRIPT_DIR}/systemd/edsys-share-tailnet-smb.socket.in" >"${tmp}/edsys-share-tailnet-smb.socket"
systemd-analyze verify "${tmp}"/*.service "${tmp}"/*.timer "${tmp}"/*.socket

if rg -n -i 'tusd|tcp 3045|CourierMedia|courier-reader|edsys-courier' \
  "${SCRIPT_DIR}" --glob '!README.md' --glob '!test-edsys-share.sh' --glob '!install-9950x.sh'; then
  echo "Retired application identifier found in active source" >&2
  exit 1
fi

grep -Fq 'path = /EdSys-Share' "${SCRIPT_DIR}/samba-share.conf"
grep -Fq '100.84.178.87' "${SCRIPT_DIR}/edsys-share.conf.example"
grep -Fq 'ListenStream=@TAILNET_LISTEN_IP@:445' "${SCRIPT_DIR}/systemd/edsys-share-tailnet-smb.socket.in"
echo "EdSys Share source validation passed."
