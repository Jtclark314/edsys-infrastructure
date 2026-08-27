#!/bin/sh
set -eu

# This launcher is the only authorized_keys command for the locked-down guest
# account. The original request remains one opaque argv value; nothing is eval'd.
exec /usr/bin/sudo -n -- \
  /usr/local/libexec/edsys-edcore-automation-backup-export \
  "${SSH_ORIGINAL_COMMAND:-}"
