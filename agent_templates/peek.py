"""Read a bundle's text for the install preview, without extracting it.

The preview exists so a person reads what they are about to install. Counts ("1
skill") are not reading: this module returns the identity text itself, every
skill's own description, and any member on request, straight from the zip that
``open_bundle`` already verified.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from .errors import TemplateError
from .yamlio import load_yaml

#: What one file view returns at most; the rest is marked truncated.
MAX_VIEW_BYTES = 200_000
SOUL_PREVIEW_BYTES = 64_000


def _decode(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        data = load_yaml(text[3:end])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def readable(bundle) -> dict[str, Any]:
    """The texts a person should read before installing, from the archive alone."""
    names = list(bundle.names)
    with zipfile.ZipFile(Path(bundle.path)) as archive:
        soul: dict[str, Any] | None = None
        if "SOUL.md" in names:
            raw = archive.read("SOUL.md")
            text = _decode(raw[:SOUL_PREVIEW_BYTES]) or ""
            soul = {"text": text, "truncated": len(raw) > SOUL_PREVIEW_BYTES, "bytes": len(raw)}

        skills = []
        for name in names:
            parts = name.split("/")
            # skills/<name>/SKILL.md, or skills/<category>/<name>/SKILL.md for grouped skills
            if len(parts) >= 3 and parts[0] == "skills" and parts[-1] == "SKILL.md":
                meta = _frontmatter(_decode(archive.read(name)) or "")
                skills.append({
                    "name": str(meta.get("name") or parts[-2]),
                    "description": str(meta.get("description") or ""),
                    "path": name,
                })

        files = []
        for info in archive.infolist():
            if info.is_dir() or info.filename == "template.yaml":
                continue
            files.append({"path": info.filename, "size": info.file_size})

    return {
        "soul": soul,
        "skills": sorted(skills, key=lambda s: s["name"]),
        "memories": [n for n in names if n.startswith("memories/")],
        "files": sorted(files, key=lambda f: f["path"]),
    }


def view(bundle, member: str) -> dict[str, Any]:
    """One member of the bundle as text, for the preview's file viewer."""
    member = str(member or "").replace("\\", "/").lstrip("/")
    if member not in bundle.names:
        raise TemplateError(f"{member!r} is not a file in this bundle")
    with zipfile.ZipFile(Path(bundle.path)) as archive:
        info = archive.getinfo(member)
        with archive.open(info) as handle:
            raw = handle.read(MAX_VIEW_BYTES + 1)
    text = _decode(raw[:MAX_VIEW_BYTES])
    return {
        "path": member,
        "size": info.file_size,
        "text": text,
        "binary": text is None,
        "truncated": info.file_size > MAX_VIEW_BYTES,
    }
