"""The template manifest: what a bundle says about itself, and how it is verified.

``template.yaml`` is the only file a bundle is allowed to have at its root, and
every other file must appear in its ``files`` index with a matching SHA-256. An
installer that cannot verify the index refuses the bundle: a template that
arrives over a link is untrusted input, and "it looked fine" is not a check.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA
from .errors import InvalidBundle
from .safety import PROFILE_ID_RE

_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}([-+][A-Za-z0-9.]+)?$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_index(root: Path, skip: set[str] | None = None) -> dict[str, str]:
    """``{relative path: sha256}`` for every file under *root*."""
    skip = skip or set()
    index: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in skip:
            continue
        index[rel] = sha256_file(path)
    return index


def verify_index(root: Path, index: dict[str, str]) -> list[str]:
    """Every listed file must exist and match. Returns the list of problems."""
    problems: list[str] = []
    for rel, expected in index.items():
        path = Path(root) / rel
        if not path.is_file():
            problems.append(f"missing file: {rel}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(f"checksum mismatch: {rel}")
    return problems


def normalize(raw: dict, *, source: str = "") -> dict:
    """Validate and fill a manifest read from a bundle."""
    if not isinstance(raw, dict):
        raise InvalidBundle(f"{source or 'template.yaml'} is not a mapping")

    schema = str(raw.get("schema") or "")
    if schema != SCHEMA:
        raise InvalidBundle(
            f"unsupported template schema {schema or '(missing)'!r}; this Hermes expects {SCHEMA!r}"
        )

    name = str(raw.get("name") or "").strip()
    if not name:
        raise InvalidBundle("template.yaml has no 'name'")
    if not PROFILE_ID_RE.match(name):
        raise InvalidBundle(
            f"template name {name!r} is not a valid profile id (lowercase letters, digits, '-', '_')"
        )

    version = str(raw.get("version") or "1.0.0").strip()
    if not _VERSION_RE.match(version):
        raise InvalidBundle(f"invalid version {version!r}")

    contents = raw.get("contents") if isinstance(raw.get("contents"), dict) else {}
    files = raw.get("files") if isinstance(raw.get("files"), dict) else {}
    clean_files: dict[str, str] = {}
    for rel, digest in files.items():
        rel_s = str(rel).replace("\\", "/")
        if rel_s.startswith("./"):
            rel_s = rel_s[2:]
        if not rel_s or not isinstance(digest, str) or len(digest) != 64:
            raise InvalidBundle(f"invalid checksum entry for {rel!r}")
        clean_files[rel_s] = digest.lower()

    env = raw.get("env_requires") if isinstance(raw.get("env_requires"), list) else []
    env_clean = []
    for item in env:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or "").strip()
        if not key or not re.match(r"^[A-Z][A-Z0-9_]{0,63}$", key):
            continue
        env_clean.append({
            "name": key,
            "description": str(item.get("description") or ""),
            "required": bool(item.get("required", True)),
        })

    def as_list(key: str) -> list[str]:
        value = contents.get(key)
        if isinstance(value, list):
            return [str(v) for v in value if str(v).strip()]
        return []

    return {
        "schema": SCHEMA,
        "name": name,
        "title": str(raw.get("title") or name),
        "version": version,
        "description": str(raw.get("description") or ""),
        "author": str(raw.get("author") or ""),
        "license": str(raw.get("license") or ""),
        "hermes_requires": str(raw.get("hermes_requires") or ""),
        "created_at": str(raw.get("created_at") or ""),
        "source_profile": str(raw.get("source_profile") or ""),
        "contents": {
            "soul": bool(contents.get("soul", False)),
            "config": bool(contents.get("config", False)),
            "skills": as_list("skills"),
            "cron": as_list("cron"),
            "mcp": as_list("mcp"),
            "plugins": as_list("plugins"),
            "memories": bool(contents.get("memories", False)),
        },
        "env_requires": env_clean,
        "files": clean_files,
        "size_bytes": int(raw.get("size_bytes") or 0),
    }


def new_manifest(**fields) -> dict:
    """A fresh manifest, filled the way the packer writes it."""
    contents = fields.pop("contents", {})
    return normalize({
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "contents": contents,
        "files": fields.pop("files", {}),
        **fields,
    })
