"""What a bundle would actually run, and how its routines enter the cron store.

Two jobs, both on the install side of the trust boundary:

* :func:`runtime_plan` reads the bundle's own ``config.yaml``, ``mcp.json`` and
  ``cron/jobs.json`` **without extracting anything** and returns what a user has to
  see before answering "install": the MCP servers and the command line each would
  start, every routine's schedule and prompt, and the settings a bundle can carry
  that change how the agent behaves (approvals, hooks, quick commands, terminal,
  model endpoint).
* :func:`import_routines` hands those routines to Hermes' own cron store through
  ``cron.job_definition.import_job_definitions`` — the same call
  ``hermes profile install`` uses — so a shipped routine arrives paused with a
  reason attached instead of being written into ``jobs.json`` by hand.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from .errors import TemplateError
from .safety import CONFIG_DROP_POLICY
from .yamlio import load_yaml

#: Settings a bundle can carry that change what the agent is allowed to do. Named,
#: not judged: the installer lists them, the user decides.
RISKY_SETTINGS: tuple[tuple[str, str], ...] = (
    ("hooks", "code that runs on agent events"),
    ("quick_commands", "custom slash commands"),
    ("terminal", "shell and terminal backend settings"),
    ("tools", "enabled toolsets and per-tool configuration"),
)
MODEL_ENDPOINT_KEYS = ("base_url", "api_base", "endpoint")

PROMPT_PREVIEW_CHARS = 400
VALUE_PREVIEW_CHARS = 300


def _show(value: Any) -> str:
    """The actual value, as the user would read it in config.yaml (capped)."""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text[:VALUE_PREVIEW_CHARS] + ("…" if len(text) > VALUE_PREVIEW_CHARS else "")


def _policy_dropped(config: dict[str, Any]) -> list[dict[str, str]]:
    """Approval/consent policy the bundle ships, with its value: removed on install."""
    return [
        {"key": key, "value": _show(config[key])}
        for key in sorted(CONFIG_DROP_POLICY)
        if key in config
    ]


def _member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return archive.read(name)
    except KeyError:
        return None


def _text(raw: bytes | None) -> str:
    return raw.decode("utf-8", "replace") if raw else ""


def _mcp_json_servers(raw: str) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(data, dict):
        for key in ("mcpServers", "servers"):
            if isinstance(data.get(key), dict):
                return data[key]
    return {}


def _describe_mcp(servers: dict, source: str) -> list[dict[str, Any]]:
    out = []
    for name, spec in sorted(servers.items()):
        if not isinstance(spec, dict):
            continue
        command = spec.get("command")
        args = spec.get("args") if isinstance(spec.get("args"), list) else []
        url = spec.get("url")
        line = " ".join([str(command)] + [str(a) for a in args]) if command else str(url or "")
        out.append({
            "name": str(name),
            "command": str(command) if command else "",
            "args": [str(a) for a in args],
            "url": str(url) if url else "",
            "line": line,
            "source": source,
            "disabled": bool(spec.get("disabled")) or spec.get("enabled") is False,
        })
    return out


def _routines(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    jobs: list = []
    if isinstance(data, list):
        jobs = data
    elif isinstance(data, dict) and isinstance(data.get("jobs"), list):
        jobs = data["jobs"]
    out = []
    for job in jobs:
        if not isinstance(job, dict) or not job.get("id"):
            continue
        schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
        display = job.get("schedule_display") or schedule.get("display") or schedule.get("expr") or ""
        prompt = str(job.get("prompt") or "")
        out.append({
            "id": str(job["id"]),
            "name": str(job.get("name") or job["id"]),
            "schedule": str(display),
            "script": str(job.get("script") or ""),
            "monitor_url": str(job.get("monitor_url") or ""),
            "prompt": prompt[:PROMPT_PREVIEW_CHARS] + ("…" if len(prompt) > PROMPT_PREVIEW_CHARS else ""),
            "ships_enabled": bool(job.get("enabled")),
        })
    return out


def _risky(config: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if not isinstance(config, dict):
        return found
    for key, why in RISKY_SETTINGS:
        if config.get(key):
            found.append({"key": key, "why": why, "value": _show(config[key])})
    model = config.get("model") if isinstance(config.get("model"), dict) else {}
    for key in MODEL_ENDPOINT_KEYS:
        if model.get(key):
            found.append({"key": f"model.{key}", "why": "a model endpoint the bundle points at itself (removed on install)",
                          "value": _show(model[key])})
    return found


def runtime_plan(bundle) -> dict[str, Any]:
    """What the bundle would run once installed, read from the archive alone."""
    with zipfile.ZipFile(Path(bundle.path)) as archive:
        config_raw = _text(_member(archive, "config.yaml"))
        mcp_raw = _text(_member(archive, "mcp.json"))
        cron_raw = _text(_member(archive, "cron/jobs.json"))
    try:
        config = load_yaml(config_raw) if config_raw else {}
    except Exception:
        config = {}
    if not isinstance(config, dict):
        config = {}
    # Hermes starts the servers under ``mcp_servers`` in config.yaml; mcp.json is listed too,
    # so nothing a bundle declares is missing from what the user reads.
    config_mcp = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
    mcp = _describe_mcp(config_mcp, "config.yaml") + _describe_mcp(_mcp_json_servers(mcp_raw), "mcp.json")
    routines = _routines(cron_raw)
    return {
        "mcp": mcp,
        "routines": routines,
        "risky": _risky(config),
        "policy_dropped": _policy_dropped(config),
        "counts": {
            "mcp": len(mcp),
            "routines": len(routines),
            "ships_enabled_routines": sum(1 for r in routines if r["ships_enabled"]),
            "risky": len(_risky(config)),
        },
    }


def shipped_routines(bundle) -> dict[str, dict]:
    """The bundle's routine records, keyed by job id, for ``import_job_definitions``."""
    with zipfile.ZipFile(Path(bundle.path)) as archive:
        raw = _text(_member(archive, "cron/jobs.json"))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    jobs: list = []
    if isinstance(data, list):
        jobs = data
    elif isinstance(data, dict) and isinstance(data.get("jobs"), list):
        jobs = data["jobs"]
    return {
        str(job["id"]): job for job in jobs
        if isinstance(job, dict) and job.get("id")
    }


def import_routines(target: Path, shipped: dict[str, dict], *, reason: str) -> int:
    """Merge *shipped* routines into the profile at *target* through Hermes' cron API.

    Unknown ids arrive paused with *reason* attached; a routine that already exists
    keeps its scheduler state, so updating a template never resumes what the user
    paused or pauses what they resumed.
    """
    if not shipped:
        return 0
    try:
        from cron import jobs as cron_jobs
        from cron.job_definition import import_job_definitions
    except ImportError as exc:  # never drop a routine silently
        raise TemplateError("this template ships routines, which need a Hermes runtime to import") from exc
    try:
        with cron_jobs.use_cron_store(Path(target)):
            import_job_definitions(shipped, paused_reason=reason)
    except (RuntimeError, ValueError) as exc:
        raise TemplateError(f"could not import the template's routines: {exc}") from exc
    return len(shipped)
