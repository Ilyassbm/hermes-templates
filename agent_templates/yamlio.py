"""YAML in and out.

Hermes ships its own YAML reader (``hermes_yaml``), and it is the one that reads
``config.yaml`` in production, so a template round-trips through exactly the same
parser the runtime uses. The fallbacks exist so the packer can be unit-tested in
a bare interpreter.
"""

from __future__ import annotations

from pathlib import Path

_yaml = None


def _module():
    global _yaml
    if _yaml is not None:
        return _yaml
    for importer in (
        lambda: __import__("hermes_yaml"),
        lambda: __import__("hermes_cli.config", fromlist=["yaml"]).yaml,
        lambda: __import__("yaml"),
    ):
        try:
            _yaml = importer()
            return _yaml
        except Exception:
            continue
    raise RuntimeError("no YAML module available")


def load_yaml(text: str):
    return _module().safe_load(text)


def load_yaml_file(path: Path):
    return load_yaml(Path(path).read_text(encoding="utf-8"))


def dump_yaml(data) -> str:
    module = _module()
    try:
        return module.safe_dump(data, sort_keys=False, allow_unicode=True)
    except TypeError:  # pragma: no cover - older dumper
        return module.safe_dump(data, sort_keys=False)
