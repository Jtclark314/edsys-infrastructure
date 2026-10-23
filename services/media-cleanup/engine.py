"""Deterministic, journaled removal of exact movie or whole-series selections.

The model selects catalogue references, never filesystem paths or API routes.
Plans and audit evidence are private runtime data, not source documents.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from backend import BackendError


class CleanupError(RuntimeError):
    """A bounded error suitable for returning to the caller."""


REF = re.compile(r"(?:movie:(?:radarr|plex)|show:(?:sonarr|plex)):[1-9][0-9]{0,11}\Z")
PLAN_ID = re.compile(r"[a-f0-9]{32}\Z")
VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".wmv", ".mpg", ".mpeg"}
SIDECAR = {".srt", ".vtt", ".sub", ".idx", ".nfo", ".jpg", ".jpeg", ".png", ".txt", ".sfv", ".url"}


def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value).casefold() if c.isalnum())


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CleanupError(message)


def checked_refs(refs: list[str]) -> list[str]:
    require(isinstance(refs, list) and 1 <= len(refs) <= 50, "Select 1..50 exact catalogue references")
    require(all(isinstance(x, str) and REF.fullmatch(x) for x in refs), "Use exact references returned by media_find")
    require(len(set(refs)) == len(refs), "Remove duplicate references from the selection")
    return refs


def descendant(path: str | Path, root: str | Path) -> bool:
    try:
        return Path(path) != Path(root) and Path(path).is_relative_to(root)
    except (ValueError, TypeError):
        return False


def no_symlinks(path: str | Path) -> None:
    p = Path(path)
    require(p.is_absolute() and ".." not in p.parts, "Unsafe or relative media path")
    for component in [*reversed(p.parents), p]:
        try:
            require(not stat.S_ISLNK(component.lstat().st_mode), "A selected path contains a symlink; inspect it manually")
        except FileNotFoundError:
            # Missing library roots are allowed for tracked, not-yet-downloaded titles.
            continue


def fingerprint(path: str | Path) -> dict:
    s = os.lstat(path)
    require(stat.S_ISREG(s.st_mode), "Selected media contains a non-regular file")
    return {"path": str(path), "device": s.st_dev, "inode": s.st_ino,
            "size": s.st_size, "mtime_ns": s.st_mtime_ns,
            "allocated": s.st_blocks * 512, "links": s.st_nlink}


def strictly_absent(path: str | Path) -> bool:
    """An unreadable or stale path is unknown, never evidence of absence."""
    no_symlinks(path)
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    return False


def identity(f: dict) -> tuple:
    return f["device"], f["inode"], f["size"], f["mtime_ns"]


class FileSystem:
    """Fixed layout. Tests inject isolated temporary roots, never tool parameters."""

    def __init__(self, media_root: Path = Path("/mnt/media"), require_mount: bool = True):
        self.media_root = media_root
        self.library_roots = {"movie": media_root / "movies", "show": media_root / "tv"}
        self.staging_root = media_root / "downloads"
        self.require_mount = require_mount

    def mount_identity(self) -> dict:
        no_symlinks(self.media_root)
        require(self.media_root.is_dir(), "The media pool is unavailable")
        if self.require_mount:
            require(os.path.ismount(self.media_root), "The media pool is not mounted; no removal is allowed")
        s = self.media_root.stat()
        try:
            branches = os.getxattr(self.media_root, "user.mergerfs.allpaths").decode().split("\0")
        except OSError:
            require(not self.require_mount, "The expected mergerfs media pool is unavailable")
            branches = [str(self.media_root)]
        rows = []
        for p in branches:
            no_symlinks(p)
            st = os.stat(p)
            rows.append({"path": p, "device": st.st_dev, "inode": st.st_ino})
        return {"device": s.st_dev, "inode": s.st_ino, "branches": sorted(rows, key=lambda x: x["path"])}

    def validate_item(self, item: dict) -> None:
        require(item.get("kind") in self.library_roots, "Unsupported media kind")
        root = Path(item.get("root", ""))
        base = self.library_roots[item["kind"]]
        require(root.is_absolute() and root.parent == base and root.name not in {"", ".", ".."},
                "A title must map to one directory immediately below its movie or TV library")
        no_symlinks(root)
        require(not root.exists() or root.is_dir(), "The selected library root is not a directory")

    def walk(self, root: Path, *, maximum: int = 50000,
             exclude_directory: Callable[[Path], bool] | None = None) -> list[Path]:
        no_symlinks(root)
        if not root.exists():
            return []
        rows = []
        def onerror(_):
            raise CleanupError("A media directory could not be inventoried completely")
        for d, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
            for name in list(dirs):
                directory = Path(d) / name
                no_symlinks(directory)
                if exclude_directory is not None and exclude_directory(directory):
                    # Prune top-down before os.walk tries to enumerate the
                    # contents. Symlink checks still apply even to exclusions.
                    dirs.remove(name)
            for name in files:
                p = Path(d) / name
                no_symlinks(p)
                require(stat.S_ISREG(p.lstat().st_mode), "A media tree contains a special file")
                rows.append(p)
                require(len(rows) <= maximum, "The file inventory exceeds the supported batch size; select fewer titles")
        return sorted(rows)

    def physical(self, path: Path) -> list[dict]:
        no_symlinks(path)
        try:
            paths = os.getxattr(path, "user.mergerfs.allpaths").decode().split("\0")
        except OSError:
            require(not self.require_mount, "A mergerfs file could not be resolved to its physical copies")
            paths = [str(path)]
        return [fingerprint(p) for p in sorted(paths)]

    def file(self, path: Path, role: str) -> dict:
        return {**fingerprint(path), "role": role, "physical": self.physical(path)}

    def tree(self, item: dict) -> list[dict]:
        self.validate_item(item)
        return [self.file(p, "library") for p in self.walk(Path(item["root"]), maximum=20000)]

    @staticmethod
    def _release_directory(name: str, item: dict) -> bool:
        if item["kind"] == "movie":
            year = item.get("year")
            if not isinstance(year, int):
                return False
            match = re.search(rf"(?<!\d){year}(?!\d)", name)
            return bool(match and normalize(name[:match.start()]) == normalize(item["title"]))
        match = re.search(r"(?i)(?:[ ._(-]s\d{1,2}(?:e\d{1,3})?|[ ._(-]season[ ._-]*\d+)", name)
        return bool(match and normalize(name[:match.start()]) == normalize(item["title"]))

    def staging_context(self, path: Path, item: dict) -> bool:
        return self._release_directory(path.stem, item) or any(
            self._release_directory(parent.name, item)
            for parent in path.parents if descendant(parent, self.staging_root))

    def copies(self, sources: list[dict], items: list[dict], catalog: list[dict] | None = None) -> tuple[list[dict], list[str]]:
        """Exact ownership plus physical identity/full SHA256; never substrings."""
        selected_refs = {item["ref"] for item in items}
        other_items = [item for item in (catalog or []) if item["ref"] not in selected_refs]

        def unselected_directory(directory: Path) -> bool:
            return any(self._release_directory(directory.name, other)
                       or self.staging_context(directory, other) for other in other_items)

        candidates = self.walk(self.staging_root, maximum=250000,
                               exclude_directory=unselected_directory)
        by_size: dict[int, list[dict]] = {}
        known = {}
        for f in sources:
            if Path(f["path"]).suffix.lower() not in VIDEO:
                continue
            by_size.setdefault(f["size"], []).append(f)
            for p in f["physical"]:
                known[(p["device"], p["inode"])] = f
        hashes = {}
        consumed = 0
        deadline = time.monotonic() + 720
        def sha(p: str) -> str:
            nonlocal consumed
            st = fingerprint(p)
            k = identity(st)
            if k in hashes:
                return hashes[k]
            require(consumed + st["size"] <= 64 * 1024**3 and time.monotonic() < deadline,
                    "Copy verification exceeds this batch's budget; preview fewer titles")
            h = hashlib.sha256()
            with open(p, "rb") as stream:
                for block in iter(lambda: stream.read(4 * 1024**2), b""):
                    require(time.monotonic() < deadline, "Copy verification timed out; preview fewer titles")
                    h.update(block)
            require(identity(fingerprint(p)) == k, "A file changed while its copy was verified; preview again")
            consumed += st["size"]
            hashes[k] = h.hexdigest()
            return hashes[k]
        selected: dict[str, dict] = {}
        directory_anchors: dict[Path, dict] = {}
        warnings = []
        for p in candidates:
            if p.suffix.lower() not in VIDEO:
                continue
            f = fingerprint(p)
            possible = by_size.get(f["size"], [])
            if not possible:
                continue
            # Byte-identical media in another title's download still belongs to
            # that other request. A hash alone is not authority to remove it.
            if any(self.staging_context(p, other) for other in other_items):
                continue
            matches = [known[(x["device"], x["inode"])] for x in self.physical(p)
                       if (x["device"], x["inode"]) in known]
            owners = [x for x in items if any(descendant(s["path"], x["root"]) for s in possible)]
            filename_match = any(normalize(p.name) == normalize(Path(s["path"]).name) for s in possible)
            if not matches and not filename_match and not any(self.staging_context(p, owner) for owner in owners):
                warnings.append("Same-size download files without exact title/release ownership were retained")
                continue
            if not matches and f["size"] in by_size:
                target_hash = sha(str(p))
                matches = [x for x in by_size[f["size"]] if sha(x["path"]) == target_hash]
            if not matches:
                continue
            selected[str(p)] = self.file(p, "download_copy")
            owners = [x for x in items if descendant(matches[0]["path"], x["root"])]
            if owners:
                for parent in p.parents:
                    if parent == self.staging_root:
                        break
                    if self._release_directory(parent.name, owners[0]):
                        directory_anchors[parent] = owners[0]
                        break
        for folder in directory_anchors:
            contents = [p for p in candidates if descendant(p, folder)]
            unmatched_video = [p for p in contents if p.suffix.lower() in VIDEO and str(p) not in selected]
            if unmatched_video:
                warnings.append("Some files in a matched download directory were retained because their media identity differs")
                continue
            for p in contents:
                if p.suffix.lower() in SIDECAR and not any(self.staging_context(p, other) for other in other_items):
                    selected.setdefault(str(p), self.file(p, "download_sidecar"))
        return sorted(selected.values(), key=lambda x: x["path"]), sorted(set(warnings))

    @staticmethod
    def space(mount: dict) -> list[dict]:
        rows = []
        devices = set()
        for branch in mount["branches"]:
            if branch["device"] in devices:
                continue
            devices.add(branch["device"])
            s = os.statvfs(branch["path"])
            rows.append({"path": branch["path"], "device": branch["device"],
                         "available": s.f_bavail * s.f_frsize, "free": s.f_bfree * s.f_frsize})
        return rows

    def unlink(self, f: dict) -> None:
        p = Path(f["path"])
        require(any(descendant(p, root) for root in [*self.library_roots.values(), self.staging_root]),
                "The file is outside the fixed media roots")
        no_symlinks(p)
        if not p.exists():
            require(all(not Path(x["path"]).exists() for x in f["physical"]),
                    "A hidden physical copy remains; inspect the media pool")
            return
        require(identity(fingerprint(p)) == identity(f), "A selected file changed after preview; inspect state")
        for old in f["physical"]:
            no_symlinks(old["path"])
            require(Path(old["path"]).exists() and identity(fingerprint(old["path"])) == identity(old),
                    "A physical copy changed after preview; inspect state")
        # Open every component relative to a no-follow directory fd. Checking
        # ancestors and then opening one absolute parent would leave a race.
        parent = os.open("/", os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in p.parent.parts[1:]:
                next_parent = os.open(component, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = next_parent
            now = os.stat(p.name, dir_fd=parent, follow_symlinks=False)
            require((now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) == identity(f), "File identity changed")
            os.unlink(p.name, dir_fd=parent)
        except OSError:
            raise CleanupError("The file's parent directories changed or became inaccessible; inspect state") from None
        finally:
            os.close(parent)
        require(all(not Path(x["path"]).exists() for x in f["physical"]),
                "A physical copy remains after unlink; inspect state")


class Store:
    def __init__(self, root: Path):
        self.root = root
        no_symlinks(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not root.is_symlink() and root.stat().st_uid == os.getuid(), "Unsafe private plan directory")
        os.chmod(root, 0o700)
        self.key_path = root / ".plan-key"
        if not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
        require(not self.key_path.is_symlink() and self.key_path.stat().st_mode & 0o077 == 0,
                "Unsafe plan signing-key permissions")
        self.key = self.key_path.read_bytes()

    def write(self, name: str, value: dict) -> None:
        path = self.root / name
        temp = self.root / (".tmp-" + secrets.token_hex(12))
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def read(self, name: str) -> dict:
        path = self.root / name
        require(not path.is_symlink() and path.is_file() and path.stat().st_size < 32 * 1024**2,
                "Plan or journal is missing or unsafe")
        return json.loads(path.read_text())

    def sign(self, value: dict) -> str:
        return hmac.new(self.key, canonical(value), hashlib.sha256).hexdigest()

    def plan(self, plan_id: str) -> dict:
        require(isinstance(plan_id, str) and bool(PLAN_ID.fullmatch(plan_id)), "Use the exact plan_id returned by media_plan")
        value = self.read(plan_id + ".plan.json")
        signature = value.pop("signature", "")
        require(hmac.compare_digest(signature, self.sign(value)), "Plan integrity check failed")
        return value


class Engine:
    def __init__(self, backend, filesystem: FileSystem | None = None, state_root: Path | None = None, clock=time.time):
        self.backend = backend
        self.fs = filesystem or FileSystem()
        self.store = Store(state_root or Path.home() / ".local/state/edsys-media-cleanup")
        self.clock = clock

    def find(self, queries: list[str], kind: str = "any", offset: int = 0) -> dict:
        require(isinstance(queries, list) and 1 <= len(queries) <= 25, "Provide 1..25 title queries")
        require(kind in {"movie", "show", "any"}, "kind must be movie, show or any")
        require(type(offset) is int and 0 <= offset <= 10000, "offset must be an integer from 0 through 10000")
        require(all(isinstance(q, str) and 1 <= len(q.strip()) <= 160 and not any(ord(c) < 32 for c in q) for q in queries),
                "Each query must be a short title, optionally including its year")
        catalog = self.backend.catalog()
        results = []
        for query in queries:
            parsed = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", query)
            title, year = (parsed[1], int(parsed[2])) if parsed else (query, None)
            n = normalize(title)
            require(bool(n), "A title query must contain letters or numbers")
            matches = []
            for item in catalog:
                if kind != "any" and item["kind"] != kind:
                    continue
                names = [normalize(item["title"]), normalize(item.get("collection_title") or "")]
                if year is not None and item.get("year") != year:
                    continue
                if not any(n in name for name in names if name):
                    continue
                matches.append({"ref": item["ref"], "kind": item["kind"], "title": item["title"],
                                "year": item.get("year"), "managed": bool(item.get("arr_service")),
                                "collection": item.get("collection_title"), "has_files": item.get("has_files"),
                                "estimated_bytes": item.get("estimated_bytes", 0),
                                "exact_title": normalize(item["title"]) == n})
            matches.sort(key=lambda x: (not x["exact_title"], x["title"], x.get("year") or 0, x["ref"]))
            more = offset + 10 < len(matches)
            results.append({"query": query, "matching_count": len(matches), "matches": matches[offset:offset + 10],
                            "offset": offset, "next_offset": offset + 10 if more else None, "truncated": more})
        return {"results": results, "selection_required": True,
                "scope": "Movies or entire TV series; no season/episode deletion"}

    @staticmethod
    def public_items(items: list[dict]) -> list[dict]:
        return [{k: x.get(k) for k in ["ref", "title", "year", "kind"]} for x in items]

    def _blockers(self, items: list[dict]) -> list[str]:
        roots = [x["root"] for x in items]
        require(len(roots) == len(set(roots)), "Two selected records share a library directory; resolve the duplicate first")
        active = self.backend.active_paths()
        blockers = []
        if any(p == root or descendant(p, root) for p in active for root in roots):
            blockers.append("One of the selected titles is currently playing; stop that playback before making a new plan")
        queued = self.backend.active_download_refs()
        if any(x["ref"] in queued for x in items):
            blockers.append("A selected title has an active or pending ARR download; resolve that queue item before removal")
        self.backend.preflight(items)
        return blockers

    def plan(self, target_refs: list[str], include_collections: bool = False, include_download_copies: bool = True) -> dict:
        checked_refs(target_refs)
        require(type(include_collections) is bool and type(include_download_copies) is bool, "Options must be booleans")
        require(len(list(self.store.root.glob("*.plan.json"))) < 128,
                "The private audit store has reached 128 plans; archive reviewed evidence before creating more")
        require(sum(x.stat().st_size for x in self.store.root.iterdir() if x.is_file()) < 256 * 1024**2,
                "The private audit store reached its 256 MiB quota; archive reviewed evidence before creating more")
        catalog = self.backend.catalog()
        by_ref = {x["ref"]: x for x in catalog}
        require(all(x in by_ref for x in target_refs), "A selected title is no longer in the catalogue; search again")
        items = [dict(by_ref[x]) for x in target_refs]
        collections = sorted({x["collection_id"] for x in items if x.get("collection_id") is not None}) if include_collections else []
        if include_collections:
            require(all(x["kind"] == "movie" for x in items), "Collection expansion applies only to movies")
            for x in catalog:
                if x.get("collection_id") in collections and x["ref"] not in {i["ref"] for i in items}:
                    items.append(dict(x))
        require(len(items) <= 100, "Collection expansion exceeds 100 titles; use smaller explicit batches")
        # A shared or nested root with an unselected record cannot be deleted safely.
        chosen = {x["ref"] for x in items}
        for item in items:
            require(not item.get("mapping_blocker"), "The selected title has an ambiguous or unsupported library mapping")
            self.fs.validate_item(item)
            for other in catalog:
                if other["ref"] in chosen:
                    continue
                require(not (other.get("root") == item["root"] or descendant(other.get("root", ""), item["root"])
                             or descendant(item["root"], other.get("root", ""))),
                        "The selected library directory overlaps another catalogue record")
        mount = self.fs.mount_identity()
        blockers = self._blockers(items)
        library = []
        for item in items:
            tree = self.fs.tree(item)
            item["relative_files"] = [{"path": str(Path(x["path"]).relative_to(item["root"])),
                                       "size": x["size"], "mtime_ns": x["mtime_ns"]} for x in tree]
            item["file_count"] = len(tree)
            library.extend(tree)
        require(len(library) <= 20000, "The selected library batch exceeds 20,000 files")
        copies, warnings = self.fs.copies(library, items, catalog) if include_download_copies else ([], ["Download-copy cleanup was omitted"])
        allfiles = library + copies
        inodes: dict[tuple, list[dict]] = {}
        for f in allfiles:
            for p in f["physical"]:
                inodes.setdefault((p["device"], p["inode"]), []).append(p)
        reclaimable = 0
        retained = 0
        for inode, paths in inodes.items():
            paths_by_name = {x["path"]: x for x in paths}
            first = paths[0]
            if len(paths_by_name) == first["links"]:
                reclaimable += first["allocated"]
            else:
                retained += first["allocated"]
        if retained:
            warnings.append("Some selected data has hard links outside the requested library/download scope; those links will be retained")
        plan_id = secrets.token_hex(16)
        value = {"version": 1, "plan_id": plan_id, "created": self.clock(), "expires": self.clock() + 1800,
                 "items": items, "collections": collections, "files": allfiles, "mount": mount,
                 "include_download_copies": include_download_copies, "blockers": blockers,
                 "warnings": warnings, "expected_reclaimed_bytes": reclaimable, "retained_link_bytes": retained,
                 "library_file_count": len(library), "download_file_count": len(copies)}
        digest = self.store.sign(value)
        self.store.write(plan_id + ".plan.json", {**value, "signature": digest})
        self.store.write(plan_id + ".journal.json", {"state": "planned", "actions": [], "result": None})
        return self._summary(value, digest)

    def _summary(self, p: dict, signature: str) -> dict:
        return {"plan_id": p["plan_id"], "selection_hash": signature, "ready": not p["blockers"],
                "recovery_of": p.get("recovery_of"),
                "expires_unix": p["expires"], "titles": self.public_items(p["items"]),
                "title_count": len(p["items"]), "whole_series": any(x["kind"] == "show" for x in p["items"]),
                "library_file_count": p["library_file_count"], "download_copy_and_sidecar_count": p["download_file_count"],
                "expected_reclaimed_bytes": p["expected_reclaimed_bytes"], "expected_reclaimed_GB": round(p["expected_reclaimed_bytes"] / 1e9, 3),
                "retained_external_hardlink_bytes": p["retained_link_bytes"], "warnings": p["warnings"], "blockers": p["blockers"],
                "requires_user_delete_request": True, "collection_expansion": bool(p["collections"])}

    def _recovery_inventory(self, source_plan_id: str) -> tuple[dict, list[dict]]:
        """Re-use only signed, previously proven staging identities after settled removal.

        Library metadata/files must already be gone. This never relaxes unlink's
        normal physical-copy guards or retries an upstream write.
        """
        source = self.store.plan(source_plan_id)
        journal = self.store.read(source_plan_id + ".journal.json")
        require(not source.get("recovery_of") and journal["state"] in {"uncertain", "partial"},
                "Recovery requires an original partial or uncertain removal plan")
        require(source["items"] and all(x.get("arr_service") in {"radarr", "sonarr"}
                                       for x in source["items"]),
                "Recovery supports only previously managed library titles")
        require(self.fs.mount_identity() == source["mount"], "The original media mount changed; inspect state")
        verification = self.backend.verify(source["items"])
        require(all(verification.get(k) is True for k in ["arr_removed", "plex_removed", "exclusions_verified"]),
                "Recovery requires verified ARR/Plex removal and import exclusions")
        live = self.backend.catalog()
        for item in source["items"]:
            self.fs.validate_item(item)
            require(self.fs.tree(item) == [], "Library files remain or arrived after removal; inspect state")
            for other in live:
                same_identity = (other.get("ref") == item["ref"] or
                                 (other.get("kind") == item["kind"] and item.get("external_id") is not None
                                  and other.get("external_id") == item["external_id"]))
                overlaps = (other.get("root") == item["root"] or descendant(other.get("root", ""), item["root"])
                            or descendant(item["root"], other.get("root", "")))
                require(not same_identity and not overlaps, "A library record was recreated after removal; inspect state")
        for f in source["files"]:
            if f["role"] == "library":
                for path in [f["path"], *[x["path"] for x in f["physical"]]]:
                    require(strictly_absent(path), "An original library copy has not settled absent; inspect state")
        files = []
        seen = set()
        for old in source["files"]:
            if old["role"] not in {"download_copy", "download_sidecar"}:
                continue
            path = Path(old["path"])
            require(source.get("include_download_copies") is True and descendant(path, self.fs.staging_root)
                    and old["path"] not in seen, "The original staging proof is invalid")
            seen.add(old["path"])
            if strictly_absent(path):
                require(all(strictly_absent(x["path"]) for x in old["physical"]),
                        "A hidden original staging copy remains; inspect state")
                continue
            require(not any(self.fs.staging_context(path, other) for other in live),
                    "An original staging file now belongs to another catalogue title")
            now = self.fs.file(path, old["role"])
            physical = lambda f: sorted((x["path"], identity(x)) for x in f["physical"])
            require(identity(now) == identity(old) and physical(now) == physical(old),
                    "An original staging identity changed; its earlier copy proof cannot be reused")
            files.append(now)
        active = self.backend.active_paths()
        roots = [x["root"] for x in source["items"]]
        require(not any(p == root or descendant(p, root) for p in active for root in roots)
                and not any(p == f["path"] for p in active for f in files),
                "A recovery target is currently playing; inspect state")
        queued = self.backend.active_download_refs()
        require(not any(x["ref"] in queued for x in source["items"]),
                "An original title has active or pending downloads; inspect state")
        return source, files

    def recovery_plan(self, source_plan_id: str) -> dict:
        require(len(list(self.store.root.glob("*.plan.json"))) < 128,
                "The private audit store has reached 128 plans; archive reviewed evidence before creating more")
        require(sum(x.stat().st_size for x in self.store.root.iterdir() if x.is_file()) < 256 * 1024**2,
                "The private audit store reached its 256 MiB quota; archive reviewed evidence before creating more")
        source, files = self._recovery_inventory(source_plan_id)
        require(bool(files), "No original download copies or sidecars remain to recover")
        inodes: dict[tuple, dict[str, dict]] = {}
        for f in files:
            for physical in f["physical"]:
                inodes.setdefault((physical["device"], physical["inode"]), {})[physical["path"]] = physical
        reclaimable = retained = 0
        for paths in inodes.values():
            first = next(iter(paths.values()))
            if len(paths) == first["links"]:
                reclaimable += first["allocated"]
            else:
                retained += first["allocated"]
        warnings = ["Recovery removes only unchanged download files proven by the original signed plan; library metadata is already absent"]
        if retained:
            warnings.append("External hard links remain; their allocated data will be retained")
        plan_id = secrets.token_hex(16)
        value = {"version": 1, "plan_id": plan_id, "created": self.clock(), "expires": self.clock() + 1800,
                 "recovery_of": source_plan_id, "source_selection_hash": self.store.sign(source),
                 "items": source["items"], "collections": [], "files": files, "mount": source["mount"],
                 "include_download_copies": True, "blockers": [], "warnings": warnings,
                 "expected_reclaimed_bytes": reclaimable, "retained_link_bytes": retained,
                 "library_file_count": 0, "download_file_count": len(files)}
        signature = self.store.sign(value)
        self.store.write(plan_id + ".plan.json", {**value, "signature": signature})
        self.store.write(plan_id + ".journal.json", {"state": "planned", "actions": [], "result": None})
        return self._summary(value, signature)

    def status(self, plan_id: str) -> dict:
        p = self.store.plan(plan_id)
        j = self.store.read(plan_id + ".journal.json")
        result = {"plan_id": plan_id, "state": j["state"], "titles": self.public_items(p["items"]),
                  "actions_completed": sum(x["state"] == "complete" for x in j["actions"]), "result": j.get("result")}
        if j["state"] == "planned":
            result["expired"] = self.clock() > p["expires"]
        if j["state"] in {"uncertain", "partial", "running"}:
            result["remaining_library_files"] = sum(Path(x["path"]).exists() for x in p["files"] if x["role"] == "library")
            result["remaining_download_files"] = sum(Path(x["path"]).exists() for x in p["files"] if x["role"] != "library")
            result["next_action"] = "Inspect this status and live catalogue before replanning; do not repeat the destructive call"
            try:
                result["live_verification"] = self.backend.verify(p["items"])
            except Exception:
                result["live_verification"] = {"available": False, "message": "Live readback is unavailable; do not retry writes"}
        return result

    def _revalidate(self, p: dict) -> None:
        require(self.clock() <= p["expires"], "This preview has expired; create a new plan")
        require(not p["blockers"], "This preview has blockers; resolve them and create a new plan")
        require(self.fs.mount_identity() == p["mount"], "The media mount changed after preview; create a new plan")
        if p.get("recovery_of"):
            source, files = self._recovery_inventory(p["recovery_of"])
            require(hmac.compare_digest(p["source_selection_hash"], self.store.sign(source)),
                    "The original signed recovery proof changed")
            require(p["items"] == source["items"] and not p["collections"] and files == p["files"],
                    "Recovery targets changed after preview; create a new recovery plan")
            return
        live = {x["ref"]: x for x in self.backend.catalog()}
        selected = {x["ref"] for x in p["items"]}
        for item in p["items"]:
            now = live.get(item["ref"])
            require(now is not None and not now.get("mapping_blocker") and all(now.get(k) == item.get(k)
                    for k in ["root", "external_id", "arr_id", "arr_service", "plex_keys", "title", "year", "kind"]),
                    "A library record changed after preview; create a new plan")
            for other in live.values():
                if other["ref"] in selected:
                    continue
                require(not (other.get("root") == item["root"] or descendant(other.get("root", ""), item["root"])
                             or descendant(item["root"], other.get("root", ""))),
                        "A new catalogue record overlaps the selected root; create a new plan")
            require(self.fs.tree(item) == [x for x in p["files"] if x["role"] == "library" and descendant(x["path"], item["root"])],
                    "The title's files changed after preview; create a new plan")
        for f in p["files"]:
            if f["role"] != "library":
                require(not any(self.fs.staging_context(Path(f["path"]), other)
                                for other in live.values() if other["ref"] not in selected),
                        "A download copy now belongs to an unselected catalogue title; create a new plan")
                require(self.fs.file(Path(f["path"]), f["role"]) == f, "A download copy changed after preview; create a new plan")
        require(not self._blockers(p["items"]), "Playback or download activity changed after preview; create a new plan")

    def execute(self, plan_id: str, selection_hash: str, user_requested_delete: bool) -> dict:
        require(user_requested_delete is True, "Execution requires the user's explicit deletion request")
        p = self.store.plan(plan_id)
        require(isinstance(selection_hash, str) and hmac.compare_digest(selection_hash, self.store.sign(p)),
                "The selection hash does not match the preview")
        lockpath = self.store.root / ".execute.lock"
        fd = os.open(lockpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CleanupError("Another media removal is in progress; inspect its status first") from None
            return self._execute_locked(p)
        finally:
            os.close(fd)

    def _execute_locked(self, p: dict) -> dict:
        journal_name = p["plan_id"] + ".journal.json"
        j = self.store.read(journal_name)
        if j["state"] == "complete":
            return j["result"]
        require(j["state"] == "planned", "This plan already started; use media_status rather than retrying writes")
        self._revalidate(p)
        before = self.fs.space(p["mount"])
        j["state"] = "running"
        j["space_before"] = before
        self.store.write(journal_name, j)

        def action(name, target, function):
            row = {"action": name, "target": target, "state": "attempting"}
            j["actions"].append(row)
            self.store.write(journal_name, j)
            value = function()
            row["state"] = "complete"
            self.store.write(journal_name, j)
            return value

        try:
            if not p.get("recovery_of") and p["collections"]:
                action("disable_collections", p["collections"], lambda: self.backend.disable_collections(p["collections"]))
            for item in p["items"]:
                if not p.get("recovery_of") and item.get("arr_service"):
                    action("remove_arr", item["ref"], lambda item=item: self.backend.delete_arr(item))
            # ARR normally removes library files. Exact-manifest cleanup also covers unmanaged Plex titles.
            def cleanup_files():
                for f in p["files"]:
                    self.fs.unlink(f)
            action("unlink_manifest_files", {"count": len(p["files"])}, cleanup_files)
            require(all(not Path(x["path"]).exists() for f in p["files"] for x in f["physical"]),
                    "Some physical media files remain")
            for item in p["items"]:
                if not p.get("recovery_of"):
                    action("remove_plex", item["ref"], lambda item=item: self.backend.delete_plex(item))
            verification = self.backend.verify(p["items"])
            require(verification.get("arr_removed") is True and verification.get("plex_removed") is True
                    and verification.get("exclusions_verified") is True, "Library removal verification is incomplete")
            after = self.fs.space(p["mount"])
            delta = sum(x["available"] for x in after) - sum(x["available"] for x in before)
            result = {"plan_id": p["plan_id"], "state": "complete", "titles": self.public_items(p["items"]),
                      "titles_removed": 0 if p.get("recovery_of") else len(p["items"]), "library_files_removed": p["library_file_count"],
                      "recovery_of": p.get("recovery_of"),
                      "download_files_removed": p["download_file_count"],
                      "allocated_bytes_unlinked": sum({(x["device"], x["inode"]): x["allocated"] for f in p["files"] for x in f["physical"]}.values()),
                      "expected_reclaimed_bytes": p["expected_reclaimed_bytes"],
                      "observed_available_bytes_delta": delta, "observed_available_GB_delta": round(delta / 1e9, 3),
                      "retained_external_hardlink_bytes": p["retained_link_bytes"],
                      "verification": verification, "warnings": p["warnings"],
                      "space_note": "Observed free space includes concurrent writes; open files or other retained links can delay reclamation"}
            j["state"], j["result"] = "complete", result
            self.store.write(journal_name, j)
            return result
        except Exception as exc:
            # An HTTP/socket failure may have applied. No hidden retry, rollback, or broad cleanup.
            j["state"] = "uncertain" if any(x["state"] == "attempting" for x in j["actions"]) else "partial"
            reason = str(exc)[:400] if isinstance(exc, (CleanupError, BackendError)) else "Private upstream details were suppressed"
            j["result"] = {"plan_id": p["plan_id"], "state": j["state"],
                           "message": "Removal stopped; inspect media_status before any further action.", "reason": reason}
            try:
                if self.fs.mount_identity() == p["mount"]:
                    stopped = self.fs.space(p["mount"])
                    j["space_stopped"] = stopped
                    j["result"]["observed_available_bytes_delta"] = sum(x["available"] for x in stopped) - sum(x["available"] for x in before)
            except Exception:
                pass
            self.store.write(journal_name, j)
            return j["result"]
