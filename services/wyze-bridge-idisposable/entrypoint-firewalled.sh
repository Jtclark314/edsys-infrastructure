#!/bin/sh
set -eu

lan_ip="${WYZE_LAN_IP:?WYZE_LAN_IP is required}"
camera_ip="${WYZE_CAMERA_IP:?WYZE_CAMERA_IP is required}"
lan_if="$(ip -o -4 addr show | awk -v ip="$lan_ip" '$4 ~ ("^" ip "/") {print $2; exit}')"

if [ -z "$lan_if" ]; then
  echo "unable to find the Wyze LAN interface" >&2
  exit 1
fi

iptables -N EDSYS_WYZE_INPUT 2>/dev/null || iptables -F EDSYS_WYZE_INPUT
iptables -C INPUT -j EDSYS_WYZE_INPUT 2>/dev/null || iptables -I INPUT 1 -j EDSYS_WYZE_INPUT
iptables -A EDSYS_WYZE_INPUT -i lo -j ACCEPT
iptables -A EDSYS_WYZE_INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A EDSYS_WYZE_INPUT -i "$lan_if" -s "$camera_ip" -j ACCEPT
iptables -A EDSYS_WYZE_INPUT -i "$lan_if" -j DROP
iptables -A EDSYS_WYZE_INPUT -j RETURN

ip6tables -N EDSYS_WYZE_INPUT 2>/dev/null || ip6tables -F EDSYS_WYZE_INPUT
ip6tables -C INPUT -j EDSYS_WYZE_INPUT 2>/dev/null || ip6tables -I INPUT 1 -j EDSYS_WYZE_INPUT
ip6tables -A EDSYS_WYZE_INPUT -i lo -j ACCEPT
ip6tables -A EDSYS_WYZE_INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -A EDSYS_WYZE_INPUT -i "$lan_if" -j DROP
ip6tables -A EDSYS_WYZE_INPUT -j RETURN

echo "installed fail-closed Wyze LAN ingress guard on ${lan_if}"
exec /usr/local/bin/wyze-bridge
