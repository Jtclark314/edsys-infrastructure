#!/usr/bin/env python3
"""Seed only an empty datastore, using the pinned application's own model.

The upstream CLI creates sample external watches on a fresh install. Its store
constructor exposes include_default_watches=False; run it before starting the
server so no external example page is ever queued. Never edit existing state.
"""

import os
from pathlib import Path


def main():
    os.umask(0o077)
    datastore_path = Path("/datastore")
    settings_path = datastore_path / "changedetection.json"
    if settings_path.is_file():
        return
    if any(datastore_path.iterdir()):
        raise RuntimeError("Nonempty datastore has no settings file; restore or inspect it before starting")

    from changedetectionio import __version__
    from changedetectionio.store import ChangeDetectionStore

    store = ChangeDetectionStore(
        datastore_path=str(datastore_path),
        include_default_watches=False,
        version_tag=__version__,
    )
    assert not store.data["watching"], "Unexpected default watches"
    settings = store.data["settings"]["application"]
    assert settings["api_access_token_enabled"] is True
    assert not settings["notification_urls"]
    assert settings["shared_diff_access"] is False
    print("Initialized empty datastore; API key required; no notification destinations")


if __name__ == "__main__":
    main()
