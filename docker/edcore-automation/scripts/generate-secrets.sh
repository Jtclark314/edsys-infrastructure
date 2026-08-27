#!/usr/bin/env bash
set -Eeuo pipefail

readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly pki_root=$secret_root/pki
readonly ca_dir=$pki_root/ca
readonly server_dir=$pki_root/servers
readonly client_dir=$pki_root/clients

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $# -eq 0 ]] || { echo "This script accepts no path overrides." >&2; exit 64; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing to generate secrets on the wrong host." >&2; exit 1; }
/usr/local/sbin/edsys-automation-source-guard --coherent
for command in htpasswd openssl; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing required command: $command" >&2; exit 1; }
done

# Never follow an attacker-controlled link while running as root. Existing
# ancestors must already be root-owned and immutable to non-root users.
for path in /etc/edsys-secrets "$secret_root"; do
  if [[ -e $path || -L $path ]]; then
    [[ -d $path && ! -L $path ]] || { echo "Secret path is not a real directory: $path" >&2; exit 1; }
    [[ $(stat -c '%u:%g' "$path") == 0:0 ]] || { echo "Secret path is not root-owned: $path" >&2; exit 1; }
    mode=$(stat -c '%a' "$path")
    (( (8#$mode & 8#022) == 0 )) || { echo "Secret path is group/world writable: $path" >&2; exit 1; }
  fi
done
if [[ -d $secret_root ]]; then
  unsafe=$(find "$secret_root" -xdev \( -type l -o \! -type d \! -type f \) -print -quit)
  [[ -z $unsafe ]] || { echo "Symlink or special file in secret tree: $unsafe" >&2; exit 1; }
  linked=$(find "$secret_root" -xdev -type f -links +1 -print -quit)
  [[ -z $linked ]] || { echo "Hard-linked file in secret tree: $linked" >&2; exit 1; }
fi

install -d -o root -g root -m 0750 \
  "$secret_root" "$pki_root" "$ca_dir" "$server_dir" "$client_dir" \
  "$secret_root/node-red" "$secret_root/influxdb" "$secret_root/healthchecks"

create_random() {
  local path=$1 bytes=$2
  if [[ ! -e "$path" ]]; then
    umask 027
    openssl rand -base64 "$bytes" | tr -d '\n' >"$path"
    printf '\n' >>"$path"
  fi
  [[ -f "$path" && ! -L "$path" && -s "$path" ]] || {
    echo "Required secret file is unsafe or empty: $path" >&2
    exit 1
  }
  chown root:root "$path"
  chmod 0440 "$path"
}

if [[ -s "$ca_dir/ca.key" && -s "$ca_dir/ca.crt" ]]; then
  : # Temporarily online for initial issuance or controlled renewal.
elif [[ ! -e "$ca_dir/ca.key" && -s "$ca_dir/ca.crt" ]]; then
  : # Expected steady state: signing key exists only in accepted offline escrow.
elif [[ -e "$ca_dir/ca.key" || -e "$ca_dir/ca.crt" ]]; then
  echo "Automation CA is incomplete; restore it from accepted escrow." >&2
  exit 1
else
  umask 077
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$ca_dir/ca.key"
  openssl req -x509 -new -sha256 -days 3650 \
    -key "$ca_dir/ca.key" \
    -subj '/CN=EdSys EdCore Automation CA/O=EdSys/' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -out "$ca_dir/ca.crt"
fi
chown root:root "$ca_dir/ca.crt"
chmod 0444 "$ca_dir/ca.crt"
if [[ -e "$ca_dir/ca.key" ]]; then
  chown root:root "$ca_dir/ca.key"
  chmod 0400 "$ca_dir/ca.key"
fi

issue_certificate() {
  local kind=$1 name=$2 san=${3:-} custody=${4:-broker}
  local directory key_usage extended_usage key_mode
  if [[ "$kind" == server ]]; then
    directory=$server_dir
    key_usage='digitalSignature,keyEncipherment'
    extended_usage='serverAuth'
  else
    directory=$client_dir
    key_usage='digitalSignature'
    extended_usage='clientAuth'
  fi
  key_mode=0440
  [[ $custody == external ]] && key_mode=0400

  local key=$directory/$name.key cert=$directory/$name.crt
  local request=$directory/.$name.csr ext=$directory/.$name.ext
  if [[ -e "$key" || -e "$cert" ]]; then
    if [[ $custody == external && ! -e $key && -s $cert ]]; then
      : # Accepted steady state after delivery, escrow proof, and online removal.
    else
      [[ -f "$key" && ! -L "$key" && -s "$key" && -f "$cert" && ! -L "$cert" && -s "$cert" ]] || {
        echo "Incomplete or unsafe certificate pair for $name" >&2
        exit 1
      }
    fi
  else
    [[ -s "$ca_dir/ca.key" ]] || {
      echo "Certificate $name is absent and the CA key is offline; use a client-generated CSR and offline signer." >&2
      exit 1
    }
    umask 077
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$key"
    openssl req -new -sha256 -key "$key" -subj "/CN=$name/O=EdSys/" -out "$request"
    {
      printf 'basicConstraints=critical,CA:FALSE\n'
      printf 'keyUsage=critical,%s\n' "$key_usage"
      printf 'extendedKeyUsage=%s\n' "$extended_usage"
      [[ -n "$san" ]] && printf 'subjectAltName=%s\n' "$san"
      printf 'subjectKeyIdentifier=hash\n'
      printf 'authorityKeyIdentifier=keyid,issuer\n'
    } >"$ext"
    openssl x509 -req -sha256 -days 397 \
      -in "$request" -CA "$ca_dir/ca.crt" -CAkey "$ca_dir/ca.key" -CAcreateserial \
      -extfile "$ext" -out "$cert" >/dev/null 2>&1
    rm -f "$request" "$ext"
  fi
  openssl verify -CAfile "$ca_dir/ca.crt" "$cert" >/dev/null
  chown root:root "$cert"
  chmod 0444 "$cert"
  if [[ -e $key ]]; then
    chown root:root "$key"
    chmod "$key_mode" "$key"
  fi
}

issue_certificate server mosquitto 'DNS:mosquitto,DNS:edcore-automation.edsys.local,IP:192.168.50.82'
issue_certificate server node-red 'DNS:node-red,DNS:edcore-automation.edsys.local,IP:192.168.50.82'
issue_certificate server influxdb 'DNS:influxdb,DNS:edcore-automation.edsys.local,IP:192.168.50.82'

for identity in mqtt-health nodered automation-runtime telegraf event-replay command-audit; do
  issue_certificate client "$identity"
done
for identity in homeassistant frigate edsys-edge-livingroom; do
  issue_certificate client "$identity" '' external
done

create_random "$secret_root/node-red/admin_password" 36
if [[ ! -s "$secret_root/node-red/admin_password_hash" ]]; then
  umask 027
  hash=$(htpasswd -niBC 12 '' <"$secret_root/node-red/admin_password" | cut -d: -f2)
  [[ "$hash" =~ ^\$2[yb]\$ ]] || { echo "Unable to create Node-RED bcrypt hash." >&2; exit 1; }
  printf '%s\n' "$hash" >"$secret_root/node-red/admin_password_hash"
fi
chown root:root "$secret_root/node-red/admin_password_hash"
chmod 0440 "$secret_root/node-red/admin_password_hash"
create_random "$secret_root/node-red/credential_secret" 64

create_random "$secret_root/influxdb/admin_password" 36
create_random "$secret_root/influxdb/admin_token" 48
# Scoped tokens are created against the initialized InfluxDB API by deploy.sh.
for pending in telegraf_token grafana_token; do
  if [[ ! -e "$secret_root/influxdb/$pending" ]]; then
    install -o root -g root -m 0440 /dev/null "$secret_root/influxdb/$pending"
  fi
  [[ -f "$secret_root/influxdb/$pending" && ! -L "$secret_root/influxdb/$pending" ]] || {
    echo "Scoped token path is unsafe: $pending" >&2
    exit 1
  }
  chown root:root "$secret_root/influxdb/$pending"
  chmod 0440 "$secret_root/influxdb/$pending"
done

if [[ -e "$ca_dir/ca.srl" ]]; then
  chown root:root "$ca_dir/ca.srl"
  chmod 0600 "$ca_dir/ca.srl"
fi

printf 'EdCore automation runtime secret files and distinct TLS identities are present under %s.\n' "$secret_root"
printf 'No credential value was displayed; scoped InfluxDB tokens remain deploy-time gated if empty.\n'
if [[ -e "$ca_dir/ca.key" ]]; then
  printf 'The CA key is temporarily online: cold-test age escrow, then remove it with finalize-online-keys.sh.\n'
fi
