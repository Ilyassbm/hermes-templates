"""This plugin's own settings, as declared by ``config_schema`` in ``plugin.yaml``.

The Desktop renders one row per declared key under Settings → Plugins and writes
``plugins.entries.hermes-templates.settings.<key>`` in the active profile; so the
values are read back from the same place, through the same helper the runtime
uses. A secret is never here: only its ``.env`` name is, and it is read with
``agent.secret_scope`` (see :func:`publish.github_token`).

Nothing in this module raises: a broken config means "use the default", because a
settings page must never be the reason a publish or an install fails.
"""

from __future__ import annotations

from typing import Any

PLUGIN_ID = "hermes-templates"

#: Fallback when `publish_repo` is empty: GitHub repository under the token's owner.
DEFAULT_PUBLISH_REPO = "agent-templates"


def _settings() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly
        from hermes_cli.plugins import _plugin_settings_entry

        entry = _plugin_settings_entry(load_config_readonly() or {}, PLUGIN_ID) or {}
    except Exception:  # noqa: BLE001 - outside Hermes, or a config we cannot parse
        return {}
    settings = entry.get("settings")
    return settings if isinstance(settings, dict) else {}


def publish_repo() -> str:
    """``owner/name`` or a bare name from the settings page; empty means the default."""
    value = _settings().get("publish_repo")
    return value.strip() if isinstance(value, str) else ""
