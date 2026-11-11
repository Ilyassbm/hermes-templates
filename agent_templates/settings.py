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


#: The plugin context bound at register(); its get_config() is the documented door
#: to ``plugins.entries.<id>.settings``. Kept so no private core helper is imported.
_CTX: Any = None


def bind(ctx: Any) -> None:
    """Called from ``register(ctx)``; after this the settings page is readable."""
    global _CTX
    _CTX = ctx


def _settings() -> dict[str, Any]:
    keys = ("publish_repo",)
    if _CTX is None:
        return {}
    out: dict[str, Any] = {}
    try:
        for key in keys:
            value = _CTX.get_config(key, None)
            if value is not None:
                out[key] = value
    except Exception:  # noqa: BLE001 - a broken setting must never break publishing
        return {}
    return out


def publish_repo() -> str:
    """``owner/name`` or a bare name from the settings page; empty means the default."""
    value = _settings().get("publish_repo")
    return value.strip() if isinstance(value, str) else ""
