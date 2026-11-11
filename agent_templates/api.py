"""The operations both surfaces call: CLI (``hermes template``) and the page.

Kept free of argparse and FastAPI so a unit test can drive exactly what a click
drives.
"""

from __future__ import annotations

import base64
from pathlib import Path

from . import publish as publisher
from . import store
from .errors import TemplateError
from .install import Bundle, fetch, install, open_bundle, preview
from .pack import Include, build
from .profiles import list_profiles

# The page may read a bundle from these roots only. The CLI has no such limit:
# the person typing the command is the one who owns the file.
def _readable_roots(home: Path) -> tuple[Path, ...]:
    roots = {Path(home).resolve()}
    try:
        roots.add(Path.home().resolve())
    except RuntimeError:  # pragma: no cover
        pass
    roots.add((Path(home) / "plugin-data" / "hermes-templates").resolve())
    return tuple(roots)


def _allowed_path(home: Path):
    roots = _readable_roots(home)

    def check(path: Path) -> bool:
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        return any(resolved == root or root in resolved.parents for root in roots)

    return check


def profiles(home: Path) -> list[dict]:
    return list_profiles(home)


def create(
    home: Path,
    profile: str,
    *,
    name: str = "",
    title: str = "",
    description: str = "",
    author: str = "",
    license: str = "",
    version: str = "1.0.0",
    skills: bool = True,
    skill_names: list[str] | None = None,
    cron: bool = True,
    memories: bool = False,
    mcp: bool = True,
    plugins: bool = True,
    out: Path | None = None,
) -> dict:
    result = build(
        home,
        profile,
        Include(skills=skills, skill_names=skill_names, cron=cron, memories=memories, mcp=mcp, plugins=plugins),
        title=title,
        description=description,
        author=author,
        license=license,
        version=version,
        name=name,
        out=out,
    )
    entry = store.record(home, result)
    payload = result.as_dict()
    payload["id"] = entry["id"]
    payload["download"] = f"/api/plugins/hermes-templates/file/{entry['id']}"
    return payload


def resolve_source(
    home: Path, source: str = "", data_base64: str = "", filename: str = "", *, restricted: bool = False
) -> Path:
    """A link, a path, or bytes from a drag and drop — always a file on disk.

    ``restricted`` is what the page passes: a browser request may only name a
    file inside the Hermes home or the user's home. The CLI passes nothing —
    the person typing the command already owns the file.
    """
    if data_base64:
        try:
            raw = base64.b64decode(data_base64.split(",")[-1], validate=True)
        except Exception as exc:
            raise TemplateError(f"upload is not valid base64: {exc}") from exc
        return store.save_upload(home, raw, filename or "upload.htemplate")
    return fetch(source, home, allow_path=_allowed_path(home) if restricted else None, restricted=restricted)


def inspect(
    home: Path, source: str = "", data_base64: str = "", filename: str = "", name: str = "",
    *, restricted: bool = False,
) -> dict:
    path = resolve_source(home, source, data_base64, filename, restricted=restricted)
    bundle = open_bundle(path)
    link = source.strip() if source.strip().startswith(("http://", "https://")) else ""
    return preview(home, bundle, name=name, source_url=link)


def apply(
    home: Path,
    source: str = "",
    data_base64: str = "",
    filename: str = "",
    *,
    name: str = "",
    force: bool = False,
    enable_plugins: bool = True,
    restricted: bool = False,
) -> dict:
    path = resolve_source(home, source, data_base64, filename, restricted=restricted)
    bundle: Bundle = open_bundle(path)
    link = source.strip() if source.strip().startswith(("http://", "https://")) else ""
    return install(home, bundle, name=name, force=force, enable_plugins=enable_plugins, source_url=link)


def library(home: Path) -> list[dict]:
    return store.library(home)


def bundle_bytes(home: Path, template_id: str) -> tuple[str, bytes]:
    entry = store.find(home, template_id)
    if not entry:
        raise TemplateError("that template is no longer on disk")
    path = Path(entry["path"])
    return path.name, path.read_bytes()

def _library_path(home: Path, template_id: str) -> Path:
    entry = store.find(home, template_id)
    if not entry:
        raise TemplateError("that template is no longer on disk")
    return Path(entry["path"])


def publish_plan(home: Path, template_id: str, repo: str = "") -> dict:
    """What Publish would do, read-only (nothing is written, nothing is created)."""
    return publisher.plan(_library_path(home, template_id), repo=repo)


def publish(home: Path, template_id: str, repo: str = "", create_repo: bool = False) -> dict:
    result = publisher.publish(_library_path(home, template_id), repo=repo, create_repo=create_repo, home=home)
    store.record_publication(home, template_id, result)
    return result