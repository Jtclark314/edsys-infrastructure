"""One-call title resolution and deletion; media changes stay in fixed adapters."""
from __future__ import annotations

import fcntl
import hmac
import os
from pathlib import Path
import re
import secrets
import time

from backend import BackendError
from engine import CleanupError, PLAN_ID, descendant, no_symlinks, normalize, require, strictly_absent, title_names, library_paths


def label(item):
    return item["title"] + (f" ({item['year']})" if item.get("year") else "")


def resolve(titles, catalog, collections=False):
    selected, ambiguous, missing = {}, [], []
    for query in titles:
        parsed = re.fullmatch(r"(.*?)\s*\((\d{4})\)\s*", query.strip())
        name, year = (parsed[1], int(parsed[2])) if parsed else (query, None)
        key = normalize(name)
        require(bool(key), "A title must contain a movie or series name, not only a year")
        eligible = [x for x in catalog if year is None or x.get("year") == year]
        exact = [x for x in eligible if any(normalize(name) == key for name in title_names(x))]
        group_key = re.sub(r"collection$", "", key)
        group = [x for x in eligible if x.get("collection_title") and
                 re.sub(r"collection$", "", normalize(x["collection_title"])) == group_key] if collections else []
        matches = group or exact or [x for x in eligible if any(key in normalize(name) for name in title_names(x))]
        if not matches:
            missing.append(query)
        elif not group and (len(matches) > 1 or not exact):
            ambiguous.append({"query": query, "matches": [label(x) for x in matches]})
        else:
            for item in matches:
                selected[item["ref"]] = item
    if collections:
        ids = {x["collection_id"] for x in selected.values() if x.get("collection_id") is not None}
        selected.update({x["ref"]: x for x in catalog if x.get("collection_id") in ids})
    require(len(selected) <= 100, "Select at most 100 titles in one request")
    return list(selected.values()), ambiguous, missing


def release_owners(catalog):
    """Index title/year identities once instead of comparing every file to every film."""
    movie_owners, show_owners = {}, {}
    for candidate in catalog:
        for name in title_names(candidate):
            if candidate["kind"] == "movie":
                movie_owners.setdefault((normalize(name), candidate.get("year")), set()).add(candidate["ref"])
            else:
                show_owners.setdefault(normalize(name), set()).add(candidate["ref"])

    def owners(name):
        found = set()
        for match in re.finditer(r"(?<!\d)\d{4}(?!\d)", name):
            found.update(movie_owners.get((normalize(name[:match.start()]), int(match[0])), ()))
        season = re.search(r"(?i)(?:[ ._(-]s\d{1,2}(?:e\d{1,3})?|[ ._(-]season[ ._-]*\d+)", name)
        if season:
            found.update(show_owners.get(normalize(name[:season.start()]), ()))
        return found
    return owners


def owned_downloads(fs, item, catalog):
    """Inventory exact title/year release folders without reading video contents."""
    files, roots, warnings = [], [], []
    owners = release_owners(catalog)
    if not fs.staging_root.exists():
        return files, roots, warnings
    no_symlinks(fs.staging_root)
    pending = [(fs.staging_root, 0)]
    visited = 0
    while pending:
        folder, depth = pending.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            warnings.append("Some download folders could not be checked")
            continue
        for entry in entries:
            visited += 1
            require(visited <= 250000, "Download directory lookup exceeded its limit")
            path = Path(entry.path)
            # A movie release must not consume a similarly named season pack.
            is_series = bool(re.search(r"(?i)(?:^|[ ._(-])(?:s\d{1,2}(?:e\d{1,3})?|season[ ._-]*\d+)(?:\b|[ ._)\-])", entry.name))
            ownership = owners(entry.name)
            matches = item["ref"] in ownership
            if item["kind"] == "movie" and is_series:
                continue
            other_owner = bool(ownership - {item["ref"]})
            if other_owner:
                if matches:
                    warnings.append("A shared download folder was retained")
                continue
            if entry.is_symlink():
                if matches:
                    warnings.append("A matching download symlink was retained")
                continue
            if matches:
                no_symlinks(path)
                paths = fs.walk(path) if entry.is_dir(follow_symlinks=False) else [path]
                if any(owners(component.name) - {item["ref"]} for p in paths
                       for component in [p, *[parent for parent in p.parents if descendant(parent, fs.staging_root)]]):
                    warnings.append("A mixed-title download folder was retained")
                    continue
                files.extend(fs.file(p, "download_copy") for p in paths)
                if entry.is_dir(follow_symlinks=False):
                    roots.append(str(path))
            elif entry.is_dir(follow_symlinks=False) and depth < 4:
                if item["kind"] == "movie" and entry.name.casefold() in {"tv", "sonarr"}:
                    continue
                pending.append((path, depth + 1))
    return files, roots, sorted(set(warnings))


def remove_empty_directories(root):
    path = Path(root)
    no_symlinks(path)
    if not path.is_dir():
        return
    for directory, _, _ in os.walk(path, topdown=False, followlinks=False):
        no_symlinks(directory)
        try:
            os.rmdir(directory)
        except OSError:
            pass  # New or intentionally retained files keep their directory.


class Deletion:
    def __init__(self, engine):
        self.engine, self.backend, self.fs, self.store = engine, engine.backend, engine.fs, engine.store

    def run(self, titles, kind="movie", include_collections=False, dry_run=False, operation_id=None):
        require(kind in {"movie", "show", "any"}, "kind must be movie, show or any")
        require(type(dry_run) is bool and type(include_collections) is bool, "Options must be booleans")
        if operation_id is not None:
            require(isinstance(operation_id, str) and PLAN_ID.fullmatch(operation_id), "Invalid operation_id")
            require(not dry_run, "Inspect an existing operation with media_status")
        else:
            require(isinstance(titles, list) and 1 <= len(titles) <= 50, "Provide 1..50 movie or whole-series titles")
            require(all(isinstance(x, str) and 0 < len(x.strip()) <= 160 and normalize(x)
                        and not any(ord(c) < 32 for c in x) for x in titles), "Provide readable titles, optionally with a year")
        lock = os.open(self.store.root / ".execute.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"state": "busy", "removed": [], "message": "Another media deletion is running"}
            if operation_id:
                record = self.store.read(operation_id + ".delete.json")
                require(hmac.compare_digest(record["signature"], self.store.sign(record["manifest"])), "Operation record changed")
                if titles is not None:
                    require(titles == record["manifest"]["titles"], "Resume with the original titles or only operation_id")
                if record["state"] == "complete":
                    return record["result"]
                catalog = self.backend.catalog(record["manifest"]["kind"])
                return self.execute(record, catalog, resuming=True)
            catalog = self.backend.catalog(kind)
            items, ambiguous, missing = resolve(titles, catalog, include_collections)
            if ambiguous:
                return {"state": "needs_clarification", "ambiguous": ambiguous, "not_found": missing, "removed": []}
            if not items:
                return {"state": "complete", "removed": [], "not_found": missing}
            mount = self.fs.mount_identity()
            entries = []
            for item in items:
                entry = {"item": dict(item), "files": [], "download_roots": [], "step": "planned", "warnings": []}
                try:
                    self.fs.validate_item(item)
                    require(not item.get("mapping_blocker"), "Ambiguous library folder mapping")
                    require(not any(other["ref"] != item["ref"] and
                                    any(a == b or descendant(a, b) or descendant(b, a)
                                        for a in library_paths(other) for b in library_paths(item)) for other in catalog),
                            "Library folder is shared with another title")
                    library = self.fs.tree(item)
                    downloads, roots, warnings = owned_downloads(self.fs, item, catalog)
                    entry.update(files=library + downloads, download_roots=roots, warnings=warnings)
                    relative_root = Path(item["root"]).parent if item.get("standalone_file") else Path(item["root"])
                    entry["item"]["relative_files"] = [{"path": str(Path(f["path"]).relative_to(relative_root)),
                                                       "size": f["size"], "mtime_ns": f["mtime_ns"]} for f in library
                                                      if item.get("standalone_file") or descendant(f["path"], item["root"])]
                    self.guard(entry["item"], kind)
                    self.backend.preflight([entry["item"]])
                except (CleanupError, BackendError, OSError) as exc:
                    entry.update(step="blocked", error=self.reason(exc))
                entries.append(entry)
            if dry_run:
                return {"state": "dry_run", "deletion_performed": False, "titles": [label(x["item"]) for x in entries],
                        "library_files": sum(f["role"] == "library" for x in entries for f in x["files"]),
                        "download_files": sum(f["role"] != "library" for x in entries for f in x["files"]),
                        "not_found": missing,
                        "skipped": [{"title": label(x["item"]), "reason": x["error"]} for x in entries if x["step"] == "blocked"],
                        "warnings": sorted({w for x in entries for w in x["warnings"]})}
            operation_id = secrets.token_hex(16)
            manifest = {"operation_id": operation_id, "titles": titles, "kind": kind, "mount": mount,
                        "entries": entries, "not_found": missing,
                        "collections": sorted({x["collection_id"] for x in items if x.get("collection_id") is not None}) if include_collections else []}
            record = {"manifest": manifest, "signature": self.store.sign(manifest), "state": "running",
                      "steps": {}, "space_before": self.space(mount)}
            self.save(record)
            return self.execute(record, catalog)
        finally:
            os.close(lock)

    @staticmethod
    def reason(exc):
        return str(exc)[:240] if isinstance(exc, (CleanupError, BackendError)) else "A media path could not be accessed"

    def save(self, record):
        self.store.write(record["manifest"]["operation_id"] + ".delete.json", record)

    def space(self, mount):
        try:
            return sum(x["available"] for x in self.fs.space(mount))
        except OSError:
            return None

    def guard(self, item, kind):
        require(not any(p == root or descendant(p, root) for p in self.backend.active_paths() for root in library_paths(item)),
                "Currently playing; left in place")
        require(item["ref"] not in self.backend.active_download_refs(kind), "Download pending; left in place")

    def execute(self, record, catalog, resuming=False):
        manifest, steps = record["manifest"], record["steps"]
        owners = release_owners(catalog)
        require(self.fs.mount_identity() == manifest["mount"], "The media mount changed; deletion stopped")
        if manifest["collections"] and not record.get("collections_done"):
            # This PUT is idempotent: it only turns off automatic additions.
            self.backend.disable_collections(manifest["collections"])
            record["collections_done"] = True
            self.save(record)
        for entry in manifest["entries"]:
            item, files = entry["item"], entry["files"]
            ref = item["ref"]
            if entry["step"] == "blocked" or steps.get(ref, {}).get("step") == "complete":
                continue
            progress = steps.setdefault(ref, {"step": "planned"})
            progress.pop("error", None)
            try:
                require(self.fs.mount_identity() == manifest["mount"], "The media mount changed; deletion stopped")
                self.guard(item, manifest["kind"])
                if resuming:
                    others = [x for x in catalog if x["ref"] != ref]
                    require(not any(set(library_paths(x)) & set(library_paths(item)) or
                                    (x["kind"] == item["kind"] and item.get("external_id") is not None
                                     and x.get("external_id") == item["external_id"]) for x in others),
                            "The title was recreated under another record; left in place")
                    require(not any(owners(component.name) - {ref} for f in files if f["role"] != "library"
                                    for component in [Path(f["path"]), *[p for p in Path(f["path"]).parents
                                                                       if descendant(p, self.fs.staging_root)]]),
                            "A download folder now belongs to another title; left in place")
                if progress["step"] in {"planned", "arr_pending"}:
                    if item.get("arr_service"):
                        if progress["step"] == "arr_pending":
                            verified = self.backend.verify([item], check_catalog=False)
                            require(verified["arr_removed"] and verified["exclusions_verified"],
                                    "Previous deletion could not be confirmed; no duplicate delete was sent")
                        else:
                            require(self.fs.tree(item) == [f for f in files if f["role"] == "library"],
                                    "The selected folder changed; left in place")
                            self.backend.preflight([item])
                            progress["step"] = "arr_pending"
                            self.save(record)
                            try:
                                self.backend.delete_arr(item)
                            except Exception:
                                verified = self.backend.verify([item], check_catalog=False)
                                require(verified["arr_removed"] and verified["exclusions_verified"],
                                        "Deletion could not be confirmed; no duplicate delete was sent")
                    progress["step"] = "files_pending"
                    self.save(record)
                if progress["step"] == "files_pending":
                    if item.get("additional_roots"):
                        self.backend.check_library_ownership(item)
                    for f in files:
                        try:
                            self.fs.unlink(f)
                        except (CleanupError, OSError):
                            # Native ARR deletion can precede the local FUSE/NFS
                            # cache settling. Poll absence only; never repeat a write.
                            if f["role"] != "library" or not item.get("arr_service"):
                                raise
                            deadline = time.monotonic() + 3
                            while time.monotonic() < deadline:
                                if strictly_absent(f["path"]) and all(strictly_absent(p["path"]) for p in f["physical"]):
                                    break
                                time.sleep(0.1)
                            else:
                                raise
                    require(not self.fs.tree(item), "New library files appeared; left in place")
                    progress["step"] = "plex_ready"
                    self.save(record)
                if progress["step"] in {"plex_ready", "plex_pending"}:
                    if progress["step"] == "plex_pending":
                        require(self.backend.verify([item], check_catalog=False)["plex_removed"],
                                "Previous Plex removal could not be confirmed; no duplicate delete was sent")
                    else:
                        progress["step"] = "plex_pending"
                        self.save(record)
                        try:
                            self.backend.delete_plex(item)
                        except Exception:
                            require(self.backend.verify([item], check_catalog=False)["plex_removed"],
                                    "Plex removal could not be confirmed; no duplicate delete was sent")
                    progress["step"] = "verify"
                    self.save(record)
                verified = self.backend.verify([item], check_catalog=False)
                require(all(verified.get(k) is True for k in ("arr_removed", "plex_removed", "exclusions_verified")),
                        "Removal is awaiting library verification")
                require(not self.fs.tree(item), "New library files appeared; left in place")
                require(all(strictly_absent(f["path"]) and all(strictly_absent(p["path"]) for p in f["physical"]) for f in files),
                        "Files appeared after deletion; left in place")
                require(not any(self.fs.walk(Path(root)) for root in entry["download_roots"]),
                        "New download files appeared; left in place")
                for root in [*library_paths(item), *entry["download_roots"]]:
                    remove_empty_directories(root)
                progress["step"] = "complete"
            except Exception as exc:
                progress["error"] = self.reason(exc) if isinstance(exc, (CleanupError, BackendError, OSError)) else "Service readback failed; deletion was not repeated"
            self.save(record)
        removed = [entry for entry in manifest["entries"] if steps.get(entry["item"]["ref"], {}).get("step") == "complete"]
        skipped = [{"title": label(x["item"]), "reason": x["error"]} for x in manifest["entries"] if x["step"] == "blocked"]
        failed = [{"title": label(x["item"]), "reason": steps[x["item"]["ref"]].get("error", "Deletion incomplete")}
                  for x in manifest["entries"] if x["step"] != "blocked" and steps.get(x["item"]["ref"], {}).get("step") != "complete"]
        result = {"operation_id": manifest["operation_id"], "state": "partial" if skipped or failed else "complete",
                  "removed": [label(x["item"]) for x in removed], "files_removed": sum(len(x["files"]) for x in removed)}
        after = self.space(manifest["mount"])
        if after is not None and record["space_before"] is not None:
            result["observed_free_space_GB_change"] = round((after - record["space_before"]) / 1e9, 3)
        for key, value in (("not_found", manifest["not_found"]), ("skipped", skipped), ("failed", failed),
                           ("warnings", sorted({w for x in manifest["entries"] for w in x["warnings"]}))):
            if value:
                result[key] = value
        record["state"], record["result"] = result["state"], result
        self.save(record)
        return result
