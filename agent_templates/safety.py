"""What may travel in a template, and what must never travel.

Two rules, applied on both sides of the wire:

* **Packing** refuses to stage a credential, a session store, a cache or a
  workspace file, at any depth — including one nested inside a skill directory.
* **Installing** refuses a bundle that carries one anyway. A published template
  that ships ``.env`` is not a template, it is a leak; the installer says so
  instead of quietly stripping it.

Everything else is text, and every text file is passed through Hermes' own
secret redactor (:func:`agent.redact.redact_sensitive_text`) before it is
written. The redactor is the same one ``hermes sessions export --redact`` uses,
so a key that never left the machine through an export cannot leave through a
template either.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .errors import UnsafeBundle

# Profile ids: the grammar Hermes itself accepts for a named profile.
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RESERVED_PROFILE_IDS = frozenset({"default", "hermes", "test", "tmp", "root", "sudo", "admin"})

# Directories that never travel, matched on any path component.
FORBIDDEN_DIRS = frozenset({
    ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", ".git", "__pycache__",
    "node_modules", "vault", "mcp-tokens", "proxy", "home", "logs", "sessions",
    "workspace", "plans", "backups", "state-snapshots", "browser-profiles",
    "browser_screenshots", "chrome-debug", "bot-desktop", "image_cache",
    "audio_cache", "document_cache", "cache", ".cache", "platforms", "pairing",
})

# Directory-name suffixes that never travel (``*_cache/``).
FORBIDDEN_DIR_SUFFIXES = ("_cache",)

# Files that never travel, matched on the file name, at any depth.
FORBIDDEN_FILES = frozenset({
    "auth.json", ".env", ".envrc", ".env.example", "credentials.json",
    "honcho.json", "mem0.json", "teams_pipeline_store.json",
    "state.db", "state.db-shm", "state.db-wal", "hermes_state.db",
    "response_store.db", "response_store.db-shm", "response_store.db-wal",
    "gateway.pid", "gateway_state.json", "gateway.lock", "processes.json",
    "auth.lock", "active_profile", ".netrc", ".npmrc", ".pypirc",
})

# Suffixes that are credentials or key material by construction.
FORBIDDEN_SUFFIXES = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk",
})

# SSH/GPG private key names (``id_rsa``, ``id_ed25519.pub`` is fine but useless).
_PRIVATE_KEY_RE = re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)")

# Path prefixes that are credential stores even though their directory names are
# ordinary (``.config/gh/hosts.yml``).
FORBIDDEN_PREFIXES = (
    ("platforms",),
    (".config", "gh"),
    (".config", "gcloud"),
    (".config", "azure"),
    (".config", "hub"),
)

# Text files that are scrubbed. Binary files (images, fonts) are copied as-is:
# they cannot carry an API key, and re-encoding them would corrupt them.
TEXT_SUFFIXES = frozenset({
    ".md", ".txt", ".yaml", ".yml", ".json", ".jsonl", ".toml", ".ini", ".cfg",
    ".conf", ".py", ".sh", ".bash", ".zsh", ".fish", ".js", ".mjs", ".cjs", ".ts",
    ".tsx", ".jsx", ".css", ".html", ".xml", ".csv", ".sql", ".env", ".example",
    ".service", ".plist", ".j2", ".jinja", ".tmpl", ".lock",
})

# Files bigger than this are refused: a template is a recipe, not an asset pack.
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 120 * 1024 * 1024
MAX_FILES = 2000
MAX_COMPRESSION_RATIO = 200


def normalize_rel(path: str) -> PurePosixPath:
    """Normalize a bundle-relative path, refusing anything that can escape the root."""
    raw = str(path).replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise UnsafeBundle(f"refused absolute path in bundle: {path!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafeBundle(f"refused path traversal in bundle: {path!r}")
    if not parts:
        raise UnsafeBundle(f"refused empty path in bundle: {path!r}")
    return PurePosixPath(*parts)


def forbidden_reason(rel: PurePosixPath | str) -> str | None:
    """Why this relative path must not travel, or ``None`` when it may."""
    p = rel if isinstance(rel, PurePosixPath) else normalize_rel(str(rel))
    parts = p.parts
    name = parts[-1]
    lowered = name.lower()

    for prefix in FORBIDDEN_PREFIXES:
        if parts[: len(prefix)] == prefix:
            return f"credential store ({'/'.join(prefix)})"
    for part in parts[:-1]:
        if part in FORBIDDEN_DIRS:
            return f"never travels ({part}/)"
        if part.endswith(FORBIDDEN_DIR_SUFFIXES):
            return f"cache directory ({part}/)"

    if lowered in FORBIDDEN_FILES:
        return "credential or runtime store"
    if lowered.startswith(".env") or lowered.startswith("auth.json"):
        return "credential or runtime store"
    if p.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "key material"
    if _PRIVATE_KEY_RE.match(lowered):
        return "private key"
    return None


def ensure_safe(rel: PurePosixPath | str) -> PurePosixPath:
    p = normalize_rel(str(rel)) if not isinstance(rel, PurePosixPath) else rel
    reason = forbidden_reason(p)
    if reason:
        raise UnsafeBundle(f"refused {p}: {reason}")
    return p


def is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.suffix == "" and path.name in {".gitignore", ".cursorrules", "LICENSE", "Makefile"}


def validate_profile_name(name: str) -> str:
    slug = (name or "").strip().lower().replace(" ", "-")
    if not PROFILE_ID_RE.match(slug):
        raise UnsafeBundle(
            f"invalid profile name {name!r}: use lowercase letters, digits, '-' and '_' (max 64)."
        )
    if slug in RESERVED_PROFILE_IDS:
        raise UnsafeBundle(f"reserved profile name {slug!r}: pick another name.")
    return slug


_REDACTOR = None
_REDACTOR_FAILED = False

# Last-resort patterns, used only when Hermes' redactor cannot be imported (a
# unit-test process outside the runtime). The runtime path always uses the real
# one: it knows every provider's key shape.
_FALLBACK_PATTERNS = (
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(ghp_[A-Za-z0-9]{20,})"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{16,})"),
)


def _redactor():
    global _REDACTOR, _REDACTOR_FAILED
    if _REDACTOR is not None or _REDACTOR_FAILED:
        return _REDACTOR
    try:  # pragma: no cover - depends on the runtime
        from agent.redact import redact_sensitive_text

        _REDACTOR = redact_sensitive_text
    except Exception:  # pragma: no cover
        _REDACTOR_FAILED = True
        _REDACTOR = None
    return _REDACTOR


def scrub_text(text: str) -> tuple[str, int]:
    """Redact secrets in *text*; return the text and how many spots were redacted."""
    redact = _redactor()
    if redact is not None:
        try:
            out = redact(text, force=True)
        except TypeError:  # older signature without ``force``
            out = redact(text)
        except Exception:  # pragma: no cover - never fail a pack on the redactor
            out = text
        if out != text:
            return out, max(1, sum(1 for a, b in zip(text.split("\n"), out.split("\n")) if a != b))
        return out, 0

    count = 0
    out = text
    for pattern in _FALLBACK_PATTERNS:
        out, n = pattern.subn(lambda m: "[REDACTED]", out)
        count += n
    return out, count


def scrub_file(path: Path) -> int:
    """Redact a text file in place. Returns the number of redacted lines (0 for binaries)."""
    if not is_text(path):
        return 0
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return 0
    cleaned, count = scrub_text(raw)
    if cleaned != raw:
        path.write_text(cleaned, encoding="utf-8")
    return count


def scrub_value(value):
    """Recursively redact every string in a parsed config structure."""
    if isinstance(value, str):
        return scrub_text(value)[0]
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    return value


# Config sections that describe *this* machine's accounts and endpoints.
# They never travel: the installer brings their own.
CONFIG_DROP_SECTIONS = frozenset({
    "platforms", "messaging", "gateway", "webhooks", "api_keys", "integrations",
    "pairing", "relay", "telemetry",
})

# Keys whose value is an endpoint or an account, not a behaviour.
CONFIG_DROP_KEYS = frozenset({"base_url", "api_mode", "api_key", "api_base", "endpoint"})

# ``model.provider`` names the author's account (``opencode-go``, ``nous``…). The
# template keeps the model *default* as a suggestion, never the account behind it.
CONFIG_DROP_MODEL_KEYS = frozenset({"provider"})


def sanitize_config(config: dict) -> tuple[dict, list[str]]:
    """Strip account/endpoint settings and redact secrets. Returns (config, dropped)."""
    dropped: list[str] = []

    def walk(node, prefix=""):
        if not isinstance(node, dict):
            return scrub_value(node)
        out = {}
        for key, value in node.items():
            dotted = f"{prefix}{key}"
            if key in CONFIG_DROP_SECTIONS and not prefix:
                dropped.append(dotted)
                continue
            if key in CONFIG_DROP_KEYS:
                dropped.append(dotted)
                continue
            if key in CONFIG_DROP_MODEL_KEYS and prefix == "model.":
                dropped.append(dotted)
                continue
            out[key] = walk(value, dotted + ".")
        return out

    return walk(config or {}), dropped
