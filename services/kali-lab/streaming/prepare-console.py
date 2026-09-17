#!/usr/bin/env python3
"""Give the unprivileged streamer the active LightDM X11 cookie at startup."""
from pathlib import Path
import os
import pwd
import stat

source = Path('/run/lightdm/root/:0')
st = source.lstat()
if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or not 0 < st.st_size < 4096:
    raise SystemExit('LightDM console authority is unavailable')
user = pwd.getpwnam('jeremy')
dest = Path('/run/edsys-kali-sunshine/Xauthority')
# The parent is a systemd-managed RuntimeDirectory; replace atomically.
tmp = dest.with_suffix('.new')
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, source.read_bytes())
    os.fchown(fd, user.pw_uid, user.pw_gid)
finally:
    os.close(fd)
os.replace(tmp, dest)
