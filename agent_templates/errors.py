"""Errors raised by the template packer and installer.

Every error carries a message written for a human reading a CLI or a desktop
card: what was refused, and why. The installer never silently drops a file the
user asked to install, and never silently accepts one it refused.
"""

from __future__ import annotations


class TemplateError(Exception):
    """Base class: a template operation was refused."""


class ProfileNotFound(TemplateError):
    """The source profile does not exist (or is not a profile at all)."""


class InvalidBundle(TemplateError):
    """The file is not a valid template bundle."""


class UnsafeBundle(TemplateError):
    """The bundle carries something that must never travel (credentials, paths...)."""


class ProfileExists(TemplateError):
    """The target profile already exists and was not created from a template."""
