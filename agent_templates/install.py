"""Open, verify and install a template bundle.

Installing is the dangerous half: the bundle comes from a link. So the order is
fixed — inspect the archive without extracting, refuse anything unsafe, verify
every checksum, and only then write into a staging directory that is renamed
into place in one move. A refused bundle leaves nothing behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import InvalidBundle, ProfileExists, TemplateError, UnsafeBundle
from .manifest import normalize, verify_index
from .profiles import layout, plugins_dirs
from .safety import (
    MAX_BUNDLE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_FILES,
    MAX_UNCOMPRESSED_BYTES,
    ensure_safe,
    forbidden_reason,
    normalize_rel,
    validate_profile_name,
)
from .yamlio import dump_yaml, load_yaml

ALLOWED_ROOT_FILES = frozenset({"template.yaml", "SOUL.md", "config.yaml", "mcp.json", "README.md"})
ALLOWED_ROOT_DIRS = frozenset({"skills", "cron", "memories"})
MARKER = ".hermes-template.json"


@dataclass
class Bundle:
    path: Path
    manifest: dict
    names: list[str]
    bytes: int

    @property
    def name(self) -> str:
        return self.manifest["name"]


def fetch(source: str, home: Path, *, timeout: int = 30, allow_path=None) -> Path:
    """Resolve a link or a local path to a bundle file on disk."""
    source = (source or "").strip()
    if not source:
        raise TemplateError("no template source given")
    if source.startswith(("http://", "https://")):
        cache = Path(home) / "plugin-data" / "hermes-templates" / "downloads"
        cache.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = cache / f"download-{stamp}-{os.getpid()}.htemplate"
        request = urllib.request.Request(source, headers={"User-Agent": "hermes-templates/1"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_BUNDLE_BYTES:
                    raise TemplateError(f"bundle too large: {length} bytes")
                written = 0
                with open(target, "wb") as handle:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_BUNDLE_BYTES:
                            handle.close()
                            target.unlink(missing_ok=True)
                            raise TemplateError("bundle too large while downloading")
                        handle.write(chunk)
        except TemplateError:
            raise
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise TemplateError(f"download failed: {exc}") from exc
        return target

    path = Path(source).expanduser()
    if allow_path is not None and not allow_path(path):
        raise TemplateError("that path is outside the folders this page may read")
    if not path.is_file():
        raise TemplateError(f"no template file at {path}")
    return path


def open_bundle(path: Path) -> Bundle:
    """Validate an archive end to end without writing anything to a profile."""
    path = Path(path)
    if not path.is_file():
        raise InvalidBundle(f"no template file at {path}")
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise InvalidBundle(f"bundle is {path.stat().st_size} bytes; the limit is {MAX_BUNDLE_BYTES}")
    if not zipfile.is_zipfile(path):
        raise InvalidBundle("not a template bundle (expected a zip archive with template.yaml)")

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise InvalidBundle(f"bundle has {len(infos)} entries; the limit is {MAX_FILES}")

        names: list[str] = []
        total = 0
        for info in infos:
            rel = normalize_rel(info.filename)
            if not info.is_dir() and rel.as_posix() != info.filename:
                raise InvalidBundle(f"unexpected path spelling in bundle: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UnsafeBundle(f"bundle contains a symlink: {info.filename}")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise InvalidBundle("bundle unpacks to more than 120 MB")
            if info.file_size > 0 and info.compress_size > 0:
                if info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                    raise InvalidBundle(f"refused a highly compressed entry (possible zip bomb): {info.filename}")
            if not info.is_dir():
                names.append(rel.as_posix())

        if "template.yaml" not in names:
            raise InvalidBundle("bundle has no template.yaml at its root")

        for name in names:
            parts = name.split("/")
            if len(parts) == 1:
                if name not in ALLOWED_ROOT_FILES:
                    raise UnsafeBundle(f"bundle carries an unexpected root file: {name}")
            elif parts[0] not in ALLOWED_ROOT_DIRS:
                raise UnsafeBundle(f"bundle carries an unexpected top-level entry: {parts[0]}/")
            reason = forbidden_reason(name)
            if reason:
                raise UnsafeBundle(
                    f"refused to install {name}: it is a {reason}. A template must never carry "
                    "credentials, sessions or caches — ask the author to rebuild it."
                )

        raw_manifest = archive.read("template.yaml").decode("utf-8", "replace")
        try:
            manifest = normalize(load_yaml(raw_manifest) or {}, source="template.yaml")
        except InvalidBundle:
            raise
        except Exception as exc:
            raise InvalidBundle(f"template.yaml is not valid YAML: {exc}") from exc

        indexed = set(manifest["files"])
        present = set(names) - {"template.yaml"}
        missing = sorted(present - indexed)
        if missing:
            raise InvalidBundle(f"bundle files are not covered by the checksum index: {', '.join(missing[:5])}")

        for rel, expected in manifest["files"].items():
            digest = hashlib.sha256()
            try:
                with archive.open(rel) as handle:
                    for chunk in iter(lambda: handle.read(1024 * 256), b""):
                        digest.update(chunk)
            except KeyError:
                raise InvalidBundle(f"checksum index names a file that is not in the bundle: {rel}") from None
            if digest.hexdigest() != expected:
                raise InvalidBundle(f"checksum mismatch for {rel}: the bundle was modified after it was built")

    return Bundle(path=path, manifest=manifest, names=sorted(names), bytes=path.stat().st_size)


def preview(home: Path, bundle: Bundle, *, name: str = "") -> dict:
    """What the installer will get, before anything is written."""
    home = Path(home)
    target_name = validate_profile_name(name or bundle.name)
    target = layout(home).profiles_dir / target_name
    exists = target.is_dir() and any((target / m).is_file() for m in ("config.yaml", "SOUL.md", "profile.yaml"))
    replaceable = exists and (target / MARKER).is_file()

    env = []
    for item in bundle.manifest["env_requires"]:
        env.append({
            **item,
            "set": bool(os.environ.get(item["name"])),
            "in_env_file": _env_has(home, target_name, item["name"]),
        })

    available, missing = _split_plugins(home, bundle.manifest["contents"]["plugins"])

    warnings: list[str] = []
    if bundle.manifest["contents"]["memories"]:
        warnings.append("This template ships memories written by its author — read them before chatting.")
    if bundle.manifest["contents"]["cron"]:
        warnings.append("Routines arrive paused: enable the ones you want in `hermes cron list`.")
    if bundle.manifest["contents"]["soul"]:
        warnings.append("SOUL.md is active as soon as you start chatting: read it first.")
    if missing:
        warnings.append("Missing plugins on this machine: " + ", ".join(missing))
    if exists and not replaceable:
        warnings.append(f"A profile named {target_name} already exists — pick another name.")

    return {
        "name": target_name,
        "manifest": bundle.manifest,
        "files": bundle.names,
        "bytes": bundle.bytes,
        "env_requires": env,
        "plugins_available": available,
        "plugins_missing": missing,
        "warnings": warnings,
        "target_path": str(target),
        "exists": exists,
        "replaceable": replaceable,
        "source": str(bundle.path),
    }


def install(
    home: Path,
    bundle: Bundle,
    *,
    name: str = "",
    force: bool = False,
    enable_plugins: bool = True,
) -> dict:
    """Write the bundle into a new profile. Returns what was installed."""
    home = Path(home)
    preview_data = preview(home, bundle, name=name)
    target_name = preview_data["name"]
    target = layout(home).profiles_dir / target_name
    exists = preview_data["exists"]
    replaceable = preview_data["replaceable"]

    if exists and not replaceable:
        raise ProfileExists(
            f"a profile named {target_name!r} already exists and was not created from a template; "
            "pick another name (the installer never overwrites your data)"
        )
    if exists and replaceable and not force:
        raise ProfileExists(f"template {target_name!r} is already installed; pass --force to update it")

    profiles_dir = layout(home).profiles_dir
    profiles_dir.mkdir(parents=True, exist_ok=True)
    # A profile deleted earlier leaves a tombstone in ``profiles/.deleted/`` that makes
    # Hermes treat a directory of that name as gone. Installing is an explicit "this
    # name again", so the tombstone goes first — otherwise the profile is written and
    # then invisible to every `-p` invocation.
    (profiles_dir / ".deleted" / target_name).unlink(missing_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".htemplate-{target_name}-", dir=profiles_dir))

    try:
        _extract(bundle, staging)
        _prepare_profile(staging, bundle.manifest, target_name, enable_plugins, home)

        problems = verify_index(staging, bundle.manifest["files"])
        if problems:
            raise InvalidBundle("installed files failed verification: " + "; ".join(problems[:5]))

        if exists:
            _replace_template_files(target, staging)
        else:
            staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    next_steps = [
        f"hermes -p {target_name} config set {item['name']} <your key>"
        for item in preview_data["env_requires"]
        if not (item["set"] or item["in_env_file"])
    ]
    return {
        "profile": target_name,
        "path": str(target),
        "next_steps": next_steps,
        "manifest": bundle.manifest,
        "plugins_enabled": preview_data["plugins_available"] if enable_plugins else [],
        "plugins_missing": preview_data["plugins_missing"],
        "env_requires": preview_data["env_requires"],
        "warnings": preview_data["warnings"],
        "replaced": bool(exists),
    }


def _extract(bundle: Bundle, dest: Path) -> None:
    """Write the bundle's files, byte for byte.

    No redaction happens here on purpose: the checksums in ``template.yaml``
    describe the exact bytes the author packed, and redacting again would
    rewrite already-redacted text (the redactor is not idempotent) and fail its
    own verification. Redaction is the packer's job; the installer's job is to
    refuse a bundle that carries a credential path at all.
    """
    with zipfile.ZipFile(bundle.path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            rel = ensure_safe(info.filename)
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)


def _prepare_profile(
    staging: Path, manifest: dict, name: str, enable_plugins: bool, home: Path
) -> None:
    config_path = staging / "config.yaml"
    config = {}
    if config_path.is_file():
        try:
            config = load_yaml(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            config = {}
    if not isinstance(config, dict):
        config = {}

    if enable_plugins and manifest["contents"]["plugins"]:
        available, _ = _split_plugins(home, manifest["contents"]["plugins"])
        if available:
            plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
            enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
            for plugin in available:
                if plugin not in enabled:
                    enabled.append(plugin)
            plugins["enabled"] = enabled
            config["plugins"] = plugins

    config_path.write_text(dump_yaml(config), encoding="utf-8")

    profile_meta = {"description": manifest["description"] or manifest["title"], "description_auto": False}
    (staging / "profile.yaml").write_text(dump_yaml(profile_meta), encoding="utf-8")

    lines = [
        f"# {manifest['title']} — keys this agent expects",
        "# Copy the ones you need into .env and fill them in. Never commit .env.",
        "",
    ]
    for item in manifest["env_requires"]:
        lines.append(f"# {item['description'] or item['name']}")
        lines.append(f"{item['name']}=")
        lines.append("")
    (staging / ".env.EXAMPLE").write_text("\n".join(lines), encoding="utf-8")

    marker = {
        "template": manifest["name"],
        "version": manifest["version"],
        "title": manifest["title"],
        "author": manifest["author"],
        "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": sorted(manifest["files"]),
    }
    (staging / MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")


def _replace_template_files(target: Path, staging: Path) -> None:
    """Update an installed template in place, leaving user data untouched."""
    try:
        previous = json.loads((target / MARKER).read_text(encoding="utf-8")).get("files") or []
    except Exception:
        previous = []
    for rel in previous:
        try:
            path = target.joinpath(*normalize_rel(str(rel)).parts)
        except UnsafeBundle:
            continue
        if path.is_file():
            path.unlink()
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(staging)
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    shutil.rmtree(staging, ignore_errors=True)


def _env_has(home: Path, profile: str, key: str) -> bool:
    for candidate in (Path(home) / ".env", Path(home) / "profiles" / profile / ".env"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.split("=", 1)[0].strip() == key:
                return True
    return False


def _split_plugins(home: Path, plugins: list[str]) -> tuple[list[str], list[str]]:
    """Which of the template's plugin ids exist on this machine (profile first, then root)."""
    dirs = plugins_dirs(home)
    available, missing = [], []
    for plugin in plugins:
        if any((base / plugin).is_dir() for base in dirs):
            available.append(plugin)
        else:
            missing.append(plugin)
    return available, missing