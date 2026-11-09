"""Build a template bundle from a profile.

The packer copies into a staging directory — never straight into the archive —
so every file passes the same two gates: the path rule (credentials, sessions,
caches, workspaces never travel) and the redactor (every text file). The bundle
is then indexed by SHA-256 and zipped.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import BUNDLE_SUFFIX
from .errors import ProfileNotFound, TemplateError
from .manifest import build_index, new_manifest
from .profiles import profile_path
from .safety import (
    MAX_BUNDLE_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    forbidden_reason,
    sanitize_config,
    scrub_file,
    scrub_text,
    validate_profile_name,
)
from .yamlio import dump_yaml, load_yaml_file

# Cron fields a template may carry. Everything else is runtime state of the
# author's machine (last run, next run, failure streak, delivery receipts).
CRON_KEEP = (
    "id", "name", "prompt", "skills", "skill", "schedule", "schedule_display",
    "repeat", "deliver", "enabled_toolsets", "no_agent", "context_from",
)
CRON_DROP_NOTE = "script, monitor_script, model/provider and run history"


@dataclass
class Include:
    skills: bool = True
    skill_names: list[str] | None = None
    cron: bool = True
    memories: bool = False
    mcp: bool = True
    plugins: bool = True


@dataclass
class BuildResult:
    path: Path
    manifest: dict
    files: int
    bytes: int
    redactions: int
    excluded: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "manifest": self.manifest,
            "files": self.files,
            "bytes": self.bytes,
            "redactions": self.redactions,
            "excluded": self.excluded,
            "notes": self.notes,
        }


def build(
    home: Path,
    profile: str,
    include: Include | None = None,
    *,
    title: str = "",
    description: str = "",
    author: str = "",
    license: str = "",
    version: str = "1.0.0",
    name: str = "",
    out: Path | None = None,
) -> BuildResult:
    """Pack *profile* into a ``.htemplate`` bundle. Returns the build report."""
    include = include or Include()
    home = Path(home)
    source = profile_path(home, profile)
    if not (source / "config.yaml").is_file():
        raise ProfileNotFound(f"profile {profile!r} not found at {source}")

    template_name = validate_profile_name(name or profile)
    excluded: list[str] = []
    notes: list[str] = []
    redactions = 0

    with tempfile.TemporaryDirectory(prefix="htemplate-build-") as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir(parents=True)

        # Identity -------------------------------------------------------
        soul = source / "SOUL.md"
        if soul.is_file():
            target = stage / "SOUL.md"
            shutil.copy2(soul, target)
            redactions += scrub_file(target)

        # Configuration --------------------------------------------------
        config, dropped = sanitize_config(load_yaml_file(source / "config.yaml") or {})
        if dropped:
            notes.append("config: stripped " + ", ".join(sorted(set(dropped))))
        (stage / "config.yaml").write_text(dump_yaml(config), encoding="utf-8")

        # Skills ---------------------------------------------------------
        skills: list[str] = []
        if include.skills:
            skills, skill_redactions = _stage_skills(
                source / "skills", stage / "skills", include.skill_names, excluded
            )
            redactions += skill_redactions

        # Routines -------------------------------------------------------
        cron_names: list[str] = []
        if include.cron:
            cron_names, cron_redactions, cron_notes = _stage_cron(source / "cron" / "jobs.json", stage)
            redactions += cron_redactions
            notes.extend(cron_notes)

        # MCP servers ----------------------------------------------------
        mcp_names: list[str] = []
        env_requires: list[dict] = []
        if include.mcp:
            mcp_names, env_requires, mcp_redactions = _stage_mcp(source / "mcp.json", stage)
            redactions += mcp_redactions

        # Memories (opt-in) ---------------------------------------------
        shipped_memories = False
        if include.memories:
            shipped_memories, memory_redactions = _stage_memories(source / "memories", stage / "memories")
            redactions += memory_redactions
            if shipped_memories:
                notes.append("memories: included — they are readable by anyone who installs this template")

        # Plugins (ids only) ---------------------------------------------
        plugins: list[str] = []
        if include.plugins and isinstance(config.get("plugins"), dict):
            enabled = config["plugins"].get("enabled")
            if isinstance(enabled, list):
                plugins = [str(p) for p in enabled]
                notes.append("plugins: ids only — the installer brings their own installs and accounts")

        manifest = new_manifest(
            name=template_name,
            title=title or template_name,
            version=version,
            description=description,
            author=author,
            license=license,
            source_profile=profile,
            contents={
                "soul": soul.is_file(),
                "config": True,
                "skills": skills,
                "cron": cron_names,
                "mcp": mcp_names,
                "plugins": plugins,
                "memories": shipped_memories,
            },
            env_requires=env_requires,
        )

        (stage / "README.md").write_text(_readme(manifest), encoding="utf-8")

        # Index + zip ----------------------------------------------------
        index = build_index(stage)
        if len(index) > MAX_FILES:
            raise TemplateError(f"too many files for a template: {len(index)} (max {MAX_FILES})")
        for rel, size in _sizes(stage).items():
            if size > MAX_FILE_BYTES:
                raise TemplateError(f"{rel} is {size} bytes; templates carry files up to 5 MB")
        manifest["files"] = index
        manifest["size_bytes"] = sum(_sizes(stage).values())
        (stage / "template.yaml").write_text(dump_yaml(manifest), encoding="utf-8")

        out = Path(out) if out else _default_output(home, template_name, version)
        out.parent.mkdir(parents=True, exist_ok=True)
        _zip(stage, out)

    size = out.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        out.unlink(missing_ok=True)
        raise TemplateError(f"bundle is {size} bytes; the limit is {MAX_BUNDLE_BYTES}")
    return BuildResult(
        path=out,
        manifest=manifest,
        files=len(index),
        bytes=size,
        redactions=redactions,
        excluded=sorted(excluded)[:40],
        notes=notes,
    )


def _default_output(home: Path, name: str, version: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(home) / "plugin-data" / "hermes-templates" / f"{name}-{version}-{stamp}{BUNDLE_SUFFIX}"


def _sizes(root: Path) -> dict[str, int]:
    return {
        p.relative_to(root).as_posix(): p.stat().st_size
        for p in Path(root).rglob("*")
        if p.is_file() and not p.is_symlink()
    }


def _zip(stage: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(stage.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, path.relative_to(stage).as_posix())


def _stage_skills(source: Path, dest: Path, only: list[str] | None, excluded: list[str]) -> tuple[list[str], int]:
    if not source.is_dir():
        return [], 0
    wanted = set(only) if only else None
    staged: list[str] = []
    redactions = 0
    dest.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if wanted is not None and entry.name not in wanted:
            continue
        if forbidden_reason(entry.name):
            excluded.append(f"{entry.name}/ (never travels)")
            continue
        target = dest / entry.name
        for item in sorted(entry.rglob("*")):
            rel = item.relative_to(entry)
            reason = forbidden_reason(f"{entry.name}/{rel.as_posix()}")
            if reason:
                excluded.append(f"{entry.name}/{rel.as_posix()} ({reason})")
                continue
            if not item.is_file() or item.is_symlink():
                continue
            if item.stat().st_size > MAX_FILE_BYTES:
                excluded.append(f"{entry.name}/{rel.as_posix()} (over 5 MB)")
                continue
            copy = target / rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, copy)
            redactions += scrub_file(copy)
        if target.is_dir():
            staged.append(entry.name)
    return sorted(staged), redactions


def _stage_cron(jobs_file: Path, stage: Path) -> tuple[list[str], int, list[str]]:
    if not jobs_file.is_file():
        return [], 0, []
    try:
        data = json.loads(jobs_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], 0, ["cron: jobs.json unreadable — skipped"]
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return [], 0, ["cron: jobs.json has no jobs list — skipped"]

    redactions = 0
    names: list[str] = []
    kept: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        entry = {}
        for key in CRON_KEEP:
            if key in job:
                entry[key] = job[key]
        if not entry.get("name"):
            continue
        if isinstance(entry.get("prompt"), str):
            entry["prompt"], n = scrub_text(entry["prompt"])
            redactions += n
        entry["enabled"] = False
        entry["state"] = "paused"
        entry["paused_at"] = None
        entry["paused_reason"] = "installed from a template — review it before enabling"
        kept.append(entry)
        names.append(str(entry["name"]))

    if not kept:
        return [], redactions, []
    target = stage / "cron"
    target.mkdir(parents=True, exist_ok=True)
    (target / "jobs.json").write_text(json.dumps({"jobs": kept}, indent=2, ensure_ascii=False), encoding="utf-8")
    return names, redactions, [f"cron: {len(kept)} routine(s) shipped paused; dropped {CRON_DROP_NOTE}"]


def _stage_mcp(mcp_file: Path, stage: Path) -> tuple[list[str], list[dict], int]:
    if not mcp_file.is_file():
        return [], [], 0
    try:
        data = json.loads(mcp_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [], 0
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        servers = data.get("servers") if isinstance(data, dict) and isinstance(data.get("servers"), dict) else {}
    if not servers:
        return [], [], 0

    env_requires: list[dict] = []
    cleaned: dict[str, dict] = {}
    redactions = 0
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        entry = {}
        for key in ("command", "args", "url", "type", "disabled"):
            if key in spec:
                value = spec[key]
                if isinstance(value, str):
                    value, n = scrub_text(value)
                    redactions += n
                elif isinstance(value, list):
                    value = [scrub_text(str(v))[0] for v in value]
                entry[key] = value
        for key in ("env", "headers"):
            block = spec.get(key)
            if isinstance(block, dict):
                for env_key in block:
                    if not any(item["name"] == env_key for item in env_requires):
                        env_requires.append({
                            "name": str(env_key),
                            "description": f"{key} for MCP server {name}",
                            "required": True,
                        })
        cleaned[str(name)] = entry

    target = stage / "mcp.json"
    target.write_text(json.dumps({"mcpServers": cleaned}, indent=2, ensure_ascii=False), encoding="utf-8")
    return sorted(cleaned), env_requires, redactions


def _stage_memories(source: Path, dest: Path) -> tuple[bool, int]:
    if not source.is_dir():
        return False, 0
    kept = False
    redactions = 0
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("MEMORY.md", "USER.md"):
        path = source / name
        if not path.is_file():
            continue
        copy = dest / name
        shutil.copy2(path, copy)
        redactions += scrub_file(copy)
        kept = True
    if not kept:
        dest.rmdir()
    return kept, redactions


def _readme(manifest: dict) -> str:
    contents = manifest["contents"]
    lines = [f"# {manifest['title']}", ""]
    if manifest["description"]:
        lines += [manifest["description"], ""]
    lines += [
        f"* Template: `{manifest['name']}` v{manifest['version']}",
        f"* Author: {manifest['author'] or 'unknown'}",
        f"* Built with Hermes {manifest.get('hermes_requires') or '>=0.23'}",
        "",
        "## What travels",
        "",
        f"- Identity: {'SOUL.md' if contents['soul'] else 'none'}",
        f"- Configuration: config.yaml (accounts and endpoints stripped)",
        f"- Skills: {', '.join(contents['skills']) or 'none'}",
        f"- Routines: {len(contents['cron'])} (installed paused)",
        f"- MCP servers: {', '.join(contents['mcp']) or 'none'}",
        f"- Plugins: {', '.join(contents['plugins']) or 'none'}",
        f"- Memories: {'included' if contents['memories'] else 'not included'}",
        "",
        "## What never travels",
        "",
        "API keys, `.env`, `auth.json`, OAuth and platform sessions, cookies, browser",
        "profiles, conversation history, logs, caches and workspace files. Every text",
        "file was passed through Hermes' secret redactor before being packed.",
        "",
        "## Install",
        "",
        "```bash",
        f"hermes template install <file-or-link>",
        "```",
        "",
        "Or open Hermes Desktop → Templates → Install, paste the link and review the",
        "preview. Routines arrive paused and plugins are never auto-enabled.",
        "",
    ]
    if manifest["env_requires"]:
        lines += ["## Keys to connect", ""]
        for item in manifest["env_requires"]:
            lines.append(f"- `{item['name']}` — {item['description'] or 'required'}")
        lines.append("")
    return "\n".join(lines)
