"""Profile discovery: which agents live in this Hermes home, and where.

Deliberately small and read-only. The packer reads a profile directory; the
installer writes a new one. Nothing here mutates an existing profile.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ProfileNotFound
from .safety import PROFILE_ID_RE, forbidden_reason


def hermes_home() -> Path:
    """The Hermes home of this process (``$HERMES_HOME`` wins, as everywhere in Hermes)."""
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        return Path(env).expanduser()
    try:  # pragma: no cover - the runtime path
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # pragma: no cover
        return Path.home() / ".hermes"


def profile_path(home: Path, name: str) -> Path:
    """Directory of a profile: ``default`` (the home itself) or ``profiles/<name>``."""
    name = (name or "default").strip()
    if name in ("", "default"):
        return Path(home)
    if not PROFILE_ID_RE.match(name):
        raise ProfileNotFound(f"unknown profile {name!r}")
    return Path(home) / "profiles" / name


def profile_exists(home: Path, name: str) -> bool:
    try:
        path = profile_path(home, name)
    except ProfileNotFound:
        return False
    return (path / "config.yaml").is_file()


def list_profiles(home: Path | None = None) -> list[dict]:
    """Every profile in this home, with the few numbers a share card needs."""
    home = Path(home or hermes_home())
    found: list[dict] = []

    if (home / "config.yaml").is_file():
        found.append(_summary(home, "default"))

    profiles_dir = home / "profiles"
    if profiles_dir.is_dir():
        for entry in sorted(profiles_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if not PROFILE_ID_RE.match(entry.name):
                continue
            if not (entry / "config.yaml").is_file():
                continue
            found.append(_summary(entry, entry.name))
    return found


def _summary(path: Path, name: str) -> dict:
    config = _load_yaml(path / "config.yaml") or {}
    description = ""
    meta = _load_yaml(path / "profile.yaml") or {}
    if isinstance(meta, dict):
        description = str(meta.get("description") or "")
    soul = path / "SOUL.md"
    skills = []
    skills_dir = path / "skills"
    if skills_dir.is_dir():
        skills = sorted(
            d.name for d in skills_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".") and not forbidden_reason(d.name)
        )
    cron = 0
    jobs = _load_json(path / "cron" / "jobs.json") or {}
    if isinstance(jobs, dict) and isinstance(jobs.get("jobs"), list):
        cron = len(jobs["jobs"])
    plugins = []
    if isinstance(config, dict):
        plugins_cfg = config.get("plugins")
        if isinstance(plugins_cfg, dict) and isinstance(plugins_cfg.get("enabled"), list):
            plugins = [str(p) for p in plugins_cfg["enabled"]]
    model = ""
    if isinstance(config, dict) and isinstance(config.get("model"), dict):
        model = str(config["model"].get("default") or "")
    return {
        "id": name,
        "label": name,
        "description": description,
        "has_soul": soul.is_file(),
        "soul_bytes": soul.stat().st_size if soul.is_file() else 0,
        "skills": skills,
        "skills_count": len(skills),
        "cron_count": cron,
        "plugins": plugins,
        "model": model,
        "path": str(path),
        "template_marker": (path / ".hermes-template.json").is_file(),
    }


def _load_yaml(path: Path):
    if not path.is_file():
        return None
    try:
        from .yamlio import load_yaml

        return load_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_json(path: Path):
    if not path.is_file():
        return None
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
