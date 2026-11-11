"""Profile discovery: which agents live in this Hermes home, and where.

A gateway usually runs *inside* one profile (``HERMES_HOME`` is
``<root>/profiles/<name>``), while the agents the person wants to share are its
siblings. So the layout is resolved once, and everything else asks this module:

* ``root``          — the Hermes home that owns ``profiles/`` (``/opt/data``)
* ``profiles_dir``  — where named profiles live
* ``own``           — the profile this process is running as, if any

Nothing here mutates an existing profile: the packer reads one, the installer
writes a new one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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


def profile_dir_for(home: Path, name: str) -> Path | None:
    """The profile directory *name* under *home*, or None.

    The name comes from a ``?profile=`` query string, i.e. from outside: it is matched
    against the profiles directory instead of being joined, so ``..``, a slash or a
    tombstoned name resolves to None rather than to another directory on disk.
    """
    raw = (name or "").strip()
    if not raw or raw.startswith(".") or "/" in raw or "\\" in raw:
        return None
    profiles = Path(home) / "profiles"
    candidate = profiles / raw
    if not candidate.is_dir() or (profiles / ".deleted" / raw).exists():
        return None
    return candidate


@dataclass(frozen=True)
class Layout:
    root: Path
    profiles_dir: Path
    own: str | None

    @property
    def home(self) -> Path:
        return self.profiles_dir / self.own if self.own else self.root


def layout(home: Path | None = None) -> Layout:
    home = Path(home or hermes_home())
    if home.parent.name == "profiles" and (home / "config.yaml").is_file():
        return Layout(root=home.parent.parent, profiles_dir=home.parent, own=home.name)
    return Layout(root=home, profiles_dir=home / "profiles", own=None)


def current_profile(home: Path | None = None) -> str:
    """The profile this process runs as (``default`` for the root home)."""
    return layout(home or hermes_home()).own or "default"


def suggest_name(home: Path, profile: str) -> str:
    """A valid template id for *profile*. ``default`` is a reserved profile id, so the
    root agent is shared as ``my-agent`` — one click, no error to decode."""
    profile = (profile or "default").strip()
    if profile in ("", "default"):
        return "my-agent"
    return profile


def profile_path(home: Path, name: str) -> Path:
    """Directory of a profile, whichever home this process runs in."""
    name = (name or "default").strip()
    info = layout(home)
    if not name or name == "default":
        return info.root
    if info.own and name == info.own:
        return info.profiles_dir / name
    if not PROFILE_ID_RE.match(name):
        raise ProfileNotFound(f"unknown profile {name!r}")
    candidate = info.profiles_dir / name
    if candidate.is_dir():
        return candidate
    nested = info.home / "profiles" / name
    if nested.is_dir():
        return nested
    return candidate


def profile_exists(home: Path, name: str) -> bool:
    try:
        path = profile_path(home, name)
    except ProfileNotFound:
        return False
    return (path / "config.yaml").is_file()


def list_profiles(home: Path | None = None) -> list[dict]:
    """Every agent this machine can share: this profile, its siblings, the root one."""
    home = Path(home or hermes_home())
    info = layout(home)
    found: list[dict] = []
    seen: set[Path] = set()

    def add(path: Path, name: str) -> None:
        resolved = path.resolve()
        if resolved in seen or not (path / "config.yaml").is_file():
            return
        seen.add(resolved)
        found.append(_summary(path, name))

    if info.own:
        add(info.profiles_dir / info.own, info.own)

    if info.profiles_dir.is_dir():
        for entry in sorted(info.profiles_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if not PROFILE_ID_RE.match(entry.name):
                continue
            add(entry, entry.name)

    if info.root != info.profiles_dir and info.root.is_dir():
        add(info.root, "default")

    return found


def plugins_dirs(home: Path) -> tuple[Path, ...]:
    """Where a plugin may live on this machine (profile first, then the root home)."""
    info = layout(home)
    dirs = []
    if info.own:
        dirs.append(info.profiles_dir / info.own / "plugins")
    dirs.append(info.root / "plugins")
    return tuple(dirs)


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
        "suggested_name": "my-agent" if name == "default" else name,
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