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
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import InvalidBundle, ProfileExists, TemplateError, UnsafeBundle
from .manifest import normalize, verify_index
from .netsafe import open_url
from .peek import readable
from .profiles import layout, plugins_dirs
from .runtime_plan import import_routines, runtime_plan, shipped_routines
from .safety import (
    MAX_BUNDLE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_FILES,
    MAX_UNCOMPRESSED_BYTES,
    ensure_safe,
    forbidden_reason,
    normalize_rel,
    sanitize_config,
    validate_profile_name,
)
from .yamlio import dump_yaml, load_yaml

ALLOWED_ROOT_FILES = frozenset({"template.yaml", "SOUL.md", "config.yaml", "mcp.json", "README.md"})
ALLOWED_ROOT_DIRS = frozenset({"skills", "cron", "memories"})
MARKER = ".hermes-template.json"
CRON_STORE = "cron/jobs.json"


@dataclass
class Bundle:
    path: Path
    manifest: dict
    names: list[str]
    bytes: int

    @property
    def name(self) -> str:
        return self.manifest["name"]


def fetch(source: str, home: Path, *, timeout: int = 30, allow_path=None, restricted: bool = False) -> Path:
    """Resolve a link or a local path to a bundle file on disk.

    Links go through :mod:`netsafe` (scheme, public-host check when ``restricted``,
    guarded redirects); the download is capped at MAX_BUNDLE_BYTES whatever the
    server claims.
    """
    source = (source or "").strip()
    if not source:
        raise TemplateError("no template source given")
    if source.startswith(("http://", "https://")):
        cache = Path(home) / "plugin-data" / "hermes-templates" / "downloads"
        cache.mkdir(parents=True, exist_ok=True)
        _prune(cache)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = cache / f"download-{stamp}-{os.getpid()}.htemplate"
        try:
            with open_url(source, timeout=timeout, restricted=restricted) as response:
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_BUNDLE_BYTES:
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
            target.unlink(missing_ok=True)
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


def _prune(cache: Path, *, keep_seconds: int = 24 * 3600) -> None:
    """Downloads are only needed for the minute between Preview and Install."""
    cutoff = time.time() - keep_seconds
    for old in cache.glob("download-*.htemplate"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


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


def version_key(version: str) -> tuple:
    """``1.10.0`` > ``1.9.0``: compare numbers, not strings. Unparseable parts sort lowest."""
    out = []
    for part in str(version).split("-")[0].split("+")[0].split("."):
        out.append(int(part) if part.isdigit() else -1)
    return tuple(out)


def installed_info(target: Path) -> dict:
    try:
        return json.loads((target / MARKER).read_text(encoding="utf-8"))
    except Exception:
        return {}


def installed_from(home: Path, link: str) -> str:
    """Name of the profile this machine already installed from *link*, or ``""``."""
    if not link:
        return ""
    profiles_dir = layout(home).profiles_dir
    if not profiles_dir.is_dir():
        return ""
    for entry in sorted(profiles_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith(".") and installed_info(entry).get("source_url") == link:
            return entry.name
    return ""


def preview(home: Path, bundle: Bundle, *, name: str = "", source_url: str = "") -> dict:
    """What the installer will get, before anything is written.

    With a link and no explicit name, a profile already installed from that same link
    is the target: reopening the link then reads "update available", not "new template".
    """
    home = Path(home)
    target_name = validate_profile_name(name or installed_from(home, source_url) or bundle.name)
    target = layout(home).profiles_dir / target_name
    exists = target.is_dir() and any((target / m).is_file() for m in ("config.yaml", "SOUL.md", "profile.yaml"))
    replaceable = exists and (target / MARKER).is_file()

    env = []
    for item in bundle.manifest["env_requires"]:
        env.append({
            **item,
            "set": key_is_set(home, target_name, item["name"]),
            "in_env_file": key_is_set(home, target_name, item["name"]),
        })

    plan = runtime_plan(bundle)

    available, missing = _split_plugins(home, bundle.manifest["contents"]["plugins"])

    warnings: list[str] = []
    if bundle.manifest["contents"]["memories"]:
        warnings.append("This template ships memories written by its author — read them before chatting.")
    if bundle.manifest["contents"]["cron"]:
        warnings.append("Routines arrive paused: enable the ones you want in `hermes cron list`.")
    if plan["mcp"]:
        names = ", ".join(server["name"] for server in plan["mcp"])
        warnings.append(
            f"MCP servers arrive disabled ({names}); each runs a command once enabled — read it above first."
        )
    if plan["risky"]:
        keys = ", ".join(f"`{item['key']}`" for item in plan["risky"])
        warnings.append(f"This bundle sets {keys}, which change what the agent may do — check them before chatting.")
    for item in plan["policy_dropped"]:
        warnings.append(
            f"Removed on install: `{item['key']}: {item['value']}` — approval and consent policy never comes "
            "from a template; this profile keeps Hermes' defaults."
        )
    if bundle.manifest["contents"]["soul"]:
        warnings.append("SOUL.md is active as soon as you start chatting: read it first.")
    if missing:
        warnings.append("Missing plugins on this machine: " + ", ".join(missing))
    if exists and not replaceable:
        warnings.append(f"A profile named {target_name} already exists — pick another name.")

    update = None
    if replaceable:
        current = installed_info(target)
        have, offered = str(current.get("version") or ""), bundle.manifest["version"]
        update = {
            "from": have,
            "to": offered,
            "newer": version_key(offered) > version_key(have),
            "same": version_key(offered) == version_key(have),
            "source_url": str(current.get("source_url") or ""),
        }
        if update["newer"]:
            warnings.insert(0, f"Update available: v{have or '?'} → v{offered}. Your data stays.")
        elif update["same"]:
            warnings.insert(0, f"Already at v{offered}.")
        else:
            warnings.insert(0, f"This is older (v{offered}) than what is installed (v{have}).")

    return {
        "name": target_name,
        "manifest": bundle.manifest,
        "files": bundle.names,
        "bytes": bundle.bytes,
        "env_requires": env,
        "plugins_available": available,
        "plugins_missing": missing,
        "warnings": warnings,
        "will_run": plan,
        "readable": readable(bundle),
        "target_path": str(target),
        "exists": exists,
        "replaceable": replaceable,
        "update": update,
        "installed_as": installed_from(home, source_url),
        "source": str(bundle.path),
    }


def install(
    home: Path,
    bundle: Bundle,
    *,
    name: str = "",
    force: bool = False,
    enable_plugins: bool = True,
    source_url: str = "",
) -> dict:
    """Write the bundle into a new profile. Returns what was installed.

    ``source_url`` is remembered next to the profile so a later preview of the same
    link can say "update available" instead of looking like a new template.
    """
    home = Path(home)
    preview_data = preview(home, bundle, name=name, source_url=source_url)
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
    # then invisible to every `-p` invocation. Core owns that file's name and shape.
    clear_profile_tombstone(target)
    staging = Path(tempfile.mkdtemp(prefix=f".htemplate-{target_name}-", dir=profiles_dir))

    try:
        _extract(bundle, staging)
        # Check the bytes exactly as extracted, before the installer rewrites config.yaml
        # (sanitized) and mcp.json (servers disabled). cron/jobs.json is not extracted at all:
        # it goes through Hermes' cron import below.
        written = {rel: digest for rel, digest in bundle.manifest["files"].items() if rel != CRON_STORE}
        problems = verify_index(staging, written)
        if problems:
            raise InvalidBundle("installed files failed verification: " + "; ".join(problems[:5]))

        _prepare_profile(staging, bundle.manifest, target_name, enable_plugins, home, source_url)

        if exists:
            _replace_template_files(target, staging)
        else:
            staging.rename(target)

        # Routines never land as a hand-written jobs.json: the shipped records go through
        # Hermes' own cron import, so unknown ids arrive paused with a reason attached.
        routines = shipped_routines(bundle)
        if routines:
            try:
                import_routines(
                    target, routines,
                    reason=f"Installed from the template {bundle.manifest['name']!r}; review it, then enable what you want.",
                )
            except Exception:
                if not exists:  # a fresh install leaves nothing half-written behind
                    shutil.rmtree(target, ignore_errors=True)
                raise
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
            if rel.as_posix() == CRON_STORE:
                # Scheduler state is not a file to copy: it is imported through the cron API
                # (see install()), which pauses what the author shipped.
                continue
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)


def _prepare_profile(
    staging: Path, manifest: dict, name: str, enable_plugins: bool, home: Path, source_url: str = ""
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
    # Same pass the packer runs, applied again on the way in: a bundle assembled by hand
    # can carry accounts, endpoints and keys the packer would have removed.
    config, _dropped = sanitize_config(config)
    # MCP servers are commands that start on the first chat. Hermes skips a server whose
    # ``enabled`` is false, so every one the bundle declares arrives off.
    if isinstance(config.get("mcp_servers"), dict):
        for spec in config["mcp_servers"].values():
            if isinstance(spec, dict):
                spec["enabled"] = False

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

    # An MCP server is a command that runs on the first chat. It arrives disabled, and the
    # preview shows the exact command line, so enabling it is a decision, not a surprise.
    mcp_path = staging / "mcp.json"
    if mcp_path.is_file():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict) and servers:
            for spec in servers.values():
                if isinstance(spec, dict):
                    spec["disabled"] = True
                    spec["enabled"] = False
            mcp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

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
        "source_url": source_url if source_url.startswith(("http://", "https://")) else "",
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
        if str(rel).replace("\\", "/") == CRON_STORE:
            continue  # the user's scheduler state, never a template file
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


def clear_profile_tombstone(target: Path) -> None:
    """Remove the tombstone that makes Hermes treat this profile name as deleted."""
    try:
        from hermes_constants import clear_named_profile_deleted

        clear_named_profile_deleted(target)
    except ImportError:  # outside a Hermes runtime
        target.parent.joinpath(".deleted", target.name).unlink(missing_ok=True)


def key_is_set(home: Path, profile: str, key: str) -> bool:
    """Whether *key* resolves for this profile, through Hermes' credential scope.

    Never opens a ``.env`` itself: the profile's scope, built by the same helper
    Hermes' dashboard routes use, is the only truthful answer, and it cannot see
    another profile's value. No ``os.environ`` fallback for the same reason.
    """
    target = layout(home).profiles_dir / profile
    if not target.is_dir():
        return False  # a profile that does not exist yet has no keys: profiles are islands
    try:
        from agent.secret_scope import (
            build_profile_secret_scope,
            get_secret,
            reset_secret_scope,
            set_secret_scope,
        )
    except ImportError:  # outside a Hermes runtime: nothing to read, so not set
        return False
    try:
        token = set_secret_scope(build_profile_secret_scope(target), profile_home=str(target))
    except Exception:  # noqa: BLE001 - an unreadable env is "not set", never a failed preview
        return False
    try:
        return bool(get_secret(key))
    except Exception:  # noqa: BLE001 - unscoped key: unknown, so not "set"
        return False
    finally:
        reset_secret_scope(token)


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