"""Agent templates: one-click share and install of a whole Hermes agent.

A template is a *recipe*, not a clone: identity (SOUL.md), configuration, skills,
routines (cron jobs, installed paused), MCP server definitions, memory files the
author explicitly publishes, and the ids of the plugins the agent expects.

It never carries credentials, sessions, conversation history, logs, caches or
workspace files. Every text file is redacted with Hermes' own secret redactor
before it is written into the bundle, and every install re-verifies the bundle
before a single byte lands in a profile directory.
"""

from __future__ import annotations

__version__ = "1.0.0"

BUNDLE_SUFFIX = ".htemplate"
SCHEMA = "hermes-template/1"
