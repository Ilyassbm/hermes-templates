"""Where built templates and uploaded bundles live between two clicks.

Everything sits under ``<HERMES_HOME>/plugin-data/hermes-templates/``: a registry
of templates this machine built, a downloads cache, and uploads. Nothing is
stored outside the Hermes home, and the registry only ever names files that are
inside it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def data_dir(home: Path) -> Path:
    path = Path(home) / "plugin-data" / "hermes-templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry_path(home: Path) -> Path:
    return data_dir(home) / "registry.json"


def load_registry(home: Path) -> dict:
    path = registry_path(home)
    if not path.is_file():
        return {"templates": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"templates": []}
    if not isinstance(data, dict) or not isinstance(data.get("templates"), list):
        return {"templates": []}
    return data


def save_registry(home: Path, data: dict) -> None:
    path = registry_path(home)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def record(home: Path, build) -> dict:
    """Remember a freshly built template so the page can offer it again."""
    data = load_registry(home)
    manifest = build.manifest
    entry = {
        "id": hashlib.sha256(f"{manifest['name']}:{manifest['version']}:{time.time()}".encode()).hexdigest()[:16],
        "name": manifest["name"],
        "title": manifest["title"],
        "version": manifest["version"],
        "description": manifest["description"],
        "author": manifest["author"],
        "path": str(build.path),
        "bytes": build.bytes,
        "files": build.files,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "contents": manifest["contents"],
        "env_requires": manifest["env_requires"],
    }
    data["templates"] = [t for t in data["templates"] if t.get("path") != entry["path"]]
    data["templates"].insert(0, entry)
    save_registry(home, data)
    return entry


def library(home: Path) -> list[dict]:
    """Registry entries whose bundle still exists on disk, newest first."""
    data = load_registry(home)
    alive = []
    for entry in data["templates"]:
        path = Path(str(entry.get("path") or ""))
        if path.is_file():
            alive.append(entry)
    if len(alive) != len(data["templates"]):
        data["templates"] = alive
        save_registry(home, data)
    return alive


def find(home: Path, template_id: str) -> dict | None:
    if not _SAFE_ID.match(template_id or ""):
        return None
    for entry in library(home):
        if entry.get("id") == template_id:
            return entry
    return None


def save_upload(home: Path, data: bytes, filename: str = "upload.htemplate") -> Path:
    """Store bytes the desktop page handed over (drag and drop)."""
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("upload is larger than 20 MB")
    suffix = ".htemplate" if not filename.lower().endswith((".htemplate", ".zip")) else ""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "upload")[:80]
    if suffix and not name.endswith(suffix):
        name += suffix
    folder = data_dir(home) / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{name}"
    path.write_bytes(data)
    return path

def record_publication(home: Path, template_id: str, result: dict) -> None:
    """Keep the link next to the template so the page can show it again."""
    data = load_registry(home)
    for entry in data["templates"]:
        if entry.get("id") == template_id:
            entry["published"] = {
                "url": result["url"],
                "pinned_url": result["pinned_url"],
                "page": result["page"],
                "repo": result["repo_full"],
                "version": result["version"],
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            break
    save_registry(home, data)
