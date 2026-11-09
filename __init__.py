"""hermes-templates — share a whole Hermes agent, or install one, in one click.

Agent half: the ``hermes template`` command tree and the REST namespace the
desktop page talks to (``dashboard/plugin_api.py``). Desktop half:
``desktop/plugin.js``, a Templates page with a Share tab and an Install tab.

The bundle is a plain zip with ``template.yaml`` at its root, every other file
indexed by SHA-256. See ``agent_templates/`` for the packer, the installer and
the rules about what never travels.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from template_cli import DESCRIPTION, HELP, handle_cli, setup_cli  # noqa: E402

SKILL_PATH = _PLUGIN_DIR / "skills" / "agent-templates" / "SKILL.md"
SKILL_DESCRIPTION = (
    "Use when someone wants to share an agent as a template, or install one from a link "
    "(.htemplate): what travels, what never does, and how to verify before installing."
)


def register(ctx) -> None:
    """Plugin entry point, called by the Hermes plugin loader."""
    try:
        ctx.register_cli_command(
            name="template", help=HELP, description=DESCRIPTION,
            setup_fn=setup_cli, handler_fn=handle_cli,
        )
    except Exception:  # noqa: BLE001 - a CLI clash must not take the desktop page down
        log.exception("hermes-templates: could not register `hermes template`")

    try:
        if SKILL_PATH.is_file():
            ctx.register_skill("agent-templates", SKILL_PATH, SKILL_DESCRIPTION)
    except Exception:  # noqa: BLE001
        log.exception("hermes-templates: could not register the agent-templates skill")


__all__ = ["register"]
