"""Tests for the template packer and installer.

Run with: /opt/hermes/.venv/bin/python -m pytest -q tests/
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from agent_templates import api, store  # noqa: E402
from agent_templates.errors import InvalidBundle, ProfileExists, UnsafeBundle  # noqa: E402
from agent_templates.install import open_bundle  # noqa: E402
from agent_templates.pack import Include, build  # noqa: E402
from agent_templates.profiles import list_profiles  # noqa: E402

SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    profile = root / "profiles" / "testbot"
    (profile / "skills" / "alpha").mkdir(parents=True)
    (profile / "skills" / "beta").mkdir(parents=True)
    (profile / "cron").mkdir(parents=True)
    (profile / "memories").mkdir(parents=True)
    (profile / "sessions").mkdir(parents=True)
    (profile / "workspace").mkdir(parents=True)

    (profile / "config.yaml").write_text(
        "model:\n"
        "  default: deepseek-v4.1-flash\n"
        "  provider: opencode-go\n"
        "  base_url: https://internal.example.com/v1\n"
        "plugins:\n"
        "  enabled:\n"
        "    - hermes-ui\n"
        "    - not-installed-here\n"
        "platforms:\n"
        "  telegram:\n"
        "    bot_token: '123:AAHfake'\n",
        encoding="utf-8",
    )
    (profile / "SOUL.md").write_text("# Test bot\n\nI answer in French.\n", encoding="utf-8")
    (profile / "profile.yaml").write_text("description: A test bot\n", encoding="utf-8")
    (profile / "skills" / "alpha" / "SKILL.md").write_text("# alpha\n\nDoes alpha things.\n", encoding="utf-8")
    (profile / "skills" / "alpha" / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    (profile / "skills" / "beta" / "SKILL.md").write_text(
        f"# beta\n\nUses the key {SECRET} to call the API.\n", encoding="utf-8"
    )
    (profile / "cron" / "jobs.json").write_text(
        json.dumps({
            "jobs": [{
                "id": "abc123", "name": "Morning brief", "prompt": "Summarise the news.",
                "schedule": {"kind": "cron", "expr": "0 8 * * *"},
                "enabled": True, "state": "scheduled", "last_run_at": "2026-01-01T08:00:00Z",
                "last_status": "ok", "next_run_at": "2026-01-02T08:00:00Z",
                "script": "/opt/data/private/run.sh", "provider": "opencode-go",
            }]
        }),
        encoding="utf-8",
    )
    (profile / "mcp.json").write_text(
        json.dumps({"mcpServers": {"search": {
            "command": "npx", "args": ["-y", "some-mcp"],
            "env": {"SEARCH_API_KEY": "super-secret-value"},
        }}}),
        encoding="utf-8",
    )
    (profile / "memories" / "MEMORY.md").write_text("# Memory\n\nI like short answers.\n", encoding="utf-8")
    (profile / ".env").write_text("OPENAI_API_KEY=sk-live-should-never-travel\n", encoding="utf-8")
    (profile / "auth.json").write_text('{"tokens": {}}', encoding="utf-8")
    (profile / "sessions" / "s1.jsonl").write_text('{"role":"user","content":"private"}\n', encoding="utf-8")
    (profile / "workspace" / "notes.md").write_text("private working notes\n", encoding="utf-8")
    (root / "plugins" / "hermes-ui").mkdir(parents=True)
    return root


def _bundle_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return sorted(n for n in archive.namelist() if not n.endswith("/"))


def test_create_leaves_credentials_and_runtime_behind(home: Path):
    result = build(home, "testbot")
    names = _bundle_names(result.path)

    assert "template.yaml" in names
    assert "SOUL.md" in names
    assert "skills/alpha/SKILL.md" in names
    assert "skills/beta/SKILL.md" in names
    assert "cron/jobs.json" in names
    assert "mcp.json" in names

    for forbidden in (".env", "auth.json", "sessions/s1.jsonl", "workspace/notes.md", "skills/alpha/.env"):
        assert forbidden not in names, forbidden
    assert not any(n.startswith("sessions/") for n in names)
    assert not any(n.startswith("workspace/") for n in names)


def test_memories_are_opt_in(home: Path):
    default = build(home, "testbot")
    assert "memories/MEMORY.md" not in _bundle_names(default.path)

    opted = build(home, "testbot", Include(memories=True))
    assert "memories/MEMORY.md" in _bundle_names(opted.path)
    assert opted.manifest["contents"]["memories"] is True


def test_secrets_are_redacted_in_text_files(home: Path):
    result = build(home, "testbot")
    with zipfile.ZipFile(result.path) as archive:
        skill = archive.read("skills/beta/SKILL.md").decode()
    assert SECRET not in skill
    assert result.redactions >= 1


def test_config_loses_accounts_and_endpoints(home: Path):
    result = build(home, "testbot")
    with zipfile.ZipFile(result.path) as archive:
        config = archive.read("config.yaml").decode()
    assert "platforms" not in config
    assert "internal.example.com" not in config
    assert "bot_token" not in config
    assert "deepseek-v4.1-flash" in config
    assert "hermes-ui" in config


def test_cron_is_shipped_paused_without_runtime_state(home: Path):
    result = build(home, "testbot")
    with zipfile.ZipFile(result.path) as archive:
        jobs = json.loads(archive.read("cron/jobs.json"))
    job = jobs["jobs"][0]
    assert job["enabled"] is False
    assert job["state"] == "paused"
    assert "last_run_at" not in job and "next_run_at" not in job
    assert "script" not in job and "provider" not in job
    assert result.manifest["contents"]["cron"] == ["Morning brief"]


def test_mcp_env_values_become_required_keys(home: Path):
    result = build(home, "testbot")
    with zipfile.ZipFile(result.path) as archive:
        mcp = json.loads(archive.read("mcp.json"))
    assert "super-secret-value" not in json.dumps(mcp)
    assert [e["name"] for e in result.manifest["env_requires"]] == ["SEARCH_API_KEY"]


def test_install_roundtrip(home: Path):
    result = build(home, "testbot")
    bundle = open_bundle(result.path)
    installed = api.apply(home, str(result.path), name="copybot")

    profile = home / "profiles" / "copybot"
    assert installed["profile"] == "copybot"
    assert (profile / "SOUL.md").read_text().startswith("# Test bot")
    assert (profile / "skills" / "alpha" / "SKILL.md").is_file()
    assert (profile / ".hermes-template.json").is_file()
    assert (profile / ".env.EXAMPLE").is_file()
    assert not (profile / ".env").exists()
    assert "OPENAI_API_KEY" not in (profile / ".env.EXAMPLE").read_text().split("=")[0]

    jobs = json.loads((profile / "cron" / "jobs.json").read_text())["jobs"]
    assert jobs[0]["enabled"] is False

    config = (profile / "config.yaml").read_text()
    assert "hermes-ui" in config
    # Plugin ids travel as a suggestion; the installer enables the ones this
    # machine actually has and names the rest instead of pretending.
    assert installed["plugins_enabled"] == ["hermes-ui"]
    assert installed["plugins_missing"] == ["not-installed-here"]

    assert bundle.manifest["name"] == "testbot"
    assert any(p["id"] == "copybot" for p in list_profiles(home))


def test_install_refuses_path_traversal(home: Path, tmp_path: Path):
    evil = tmp_path / "evil.htemplate"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("template.yaml", "schema: hermes-template/1\nname: evil\n")
        archive.writestr("../escape.txt", "pwned")
    with pytest.raises(UnsafeBundle):
        open_bundle(evil)


def test_install_refuses_absolute_paths(home: Path, tmp_path: Path):
    evil = tmp_path / "evil2.htemplate"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("template.yaml", "schema: hermes-template/1\nname: evil\n")
        archive.writestr("/etc/passwd", "root:x:0:0")
    with pytest.raises(UnsafeBundle):
        open_bundle(evil)


def test_install_refuses_a_bundle_carrying_credentials(home: Path, tmp_path: Path):
    leaky = tmp_path / "leaky.htemplate"
    with zipfile.ZipFile(leaky, "w") as archive:
        archive.writestr("template.yaml", "schema: hermes-template/1\nname: leaky\n")
        archive.writestr(".env", "OPENAI_API_KEY=sk-live\n")
    with pytest.raises(UnsafeBundle) as excinfo:
        open_bundle(leaky)
    assert ".env" in str(excinfo.value)


def test_install_refuses_a_tampered_bundle(home: Path, tmp_path: Path):
    result = build(home, "testbot")
    tampered = tmp_path / "tampered.htemplate"
    with zipfile.ZipFile(result.path) as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename == "SOUL.md":
                payload = b"# Hijacked\n"
            target.writestr(item.filename, payload)
    with pytest.raises(InvalidBundle) as excinfo:
        open_bundle(tampered)
    assert "checksum" in str(excinfo.value).lower()


def test_install_refuses_wrong_schema(home: Path, tmp_path: Path):
    bad = tmp_path / "bad.htemplate"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("template.yaml", "schema: something-else/2\nname: bad\n")
    with pytest.raises(InvalidBundle):
        open_bundle(bad)


def test_name_collision_is_refused_then_forced(home: Path):
    result = build(home, "testbot")
    api.apply(home, str(result.path), name="copybot")
    with pytest.raises(ProfileExists):
        api.apply(home, str(result.path), name="copybot")
    updated = api.apply(home, str(result.path), name="copybot", force=True)
    assert updated["replaced"] is True


def test_install_never_overwrites_a_hand_made_profile(home: Path):
    handmade = home / "profiles" / "mine"
    handmade.mkdir(parents=True)
    (handmade / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")
    (handmade / "SOUL.md").write_text("# mine\n", encoding="utf-8")

    result = build(home, "testbot")
    with pytest.raises(ProfileExists):
        api.apply(home, str(result.path), name="mine", force=True)
    assert (handmade / "SOUL.md").read_text() == "# mine\n"


def test_preview_reports_env_and_plugins(home: Path, monkeypatch):
    result = build(home, "testbot")
    monkeypatch.setenv("SEARCH_API_KEY", "set")
    data = api.inspect(home, str(result.path))
    assert data["name"] == "testbot"
    assert data["env_requires"][0]["set"] is True
    assert data["plugins_missing"] == ["not-installed-here"]
    assert any("paused" in w for w in data["warnings"])


def test_library_and_file_roundtrip(home: Path):
    created = api.create(home, "testbot", title="Test Bot")
    entries = store.library(home)
    assert entries and entries[0]["id"] == created["id"]
    name, payload = api.bundle_bytes(home, created["id"])
    assert name.endswith(".htemplate")
    assert payload[:2] == b"PK"


def test_zip_bomb_ratio_is_refused(home: Path, tmp_path: Path):
    bomb = tmp_path / "bomb.htemplate"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("template.yaml", "schema: hermes-template/1\nname: bomb\n")
        archive.writestr("skills/x/big.txt", b"0" * (4 * 1024 * 1024))
    with pytest.raises(InvalidBundle):
        open_bundle(bomb)


def test_install_clears_a_deleted_profile_tombstone(home: Path):
    result = build(home, "testbot")
    api.apply(home, str(result.path), name="copybot")
    (home / "profiles" / ".deleted").mkdir(parents=True, exist_ok=True)
    (home / "profiles" / ".deleted" / "copybot").write_text("")
    updated = api.apply(home, str(result.path), name="copybot", force=True)
    assert updated["replaced"] is True
    assert not (home / "profiles" / ".deleted" / "copybot").exists()


def test_install_returns_the_keys_to_connect(home: Path):
    result = build(home, "testbot")
    installed = api.apply(home, str(result.path), name="copybot")
    assert installed["next_steps"] == ["hermes -p copybot config set SEARCH_API_KEY <your key>"]


def test_a_profile_home_sees_its_siblings(home: Path, tmp_path: Path):
    """A gateway runs inside one profile; the agents to share are its siblings."""
    sibling = home / "profiles" / "otherbot"
    (sibling / "skills").mkdir(parents=True)
    (sibling / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")
    from agent_templates.profiles import layout, list_profiles, profile_path

    info = layout(home / "profiles" / "testbot")
    assert info.root == home
    assert info.own == "testbot"
    assert profile_path(home / "profiles" / "testbot", "otherbot") == sibling
    ids = [p["id"] for p in list_profiles(home / "profiles" / "testbot")]
    assert ids[:2] == ["testbot", "otherbot"]
    assert "default" not in ids  # no config.yaml at the root in this fixture


def test_the_root_agent_can_be_shared_without_a_reserved_id(home: Path):
    """`default` is a reserved profile id; sharing the root agent must still be one click."""
    (home / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")
    (home / "SOUL.md").write_text("# root agent\n", encoding="utf-8")
    result = build(home, "default")
    assert result.manifest["name"] == "my-agent"
    assert "SOUL.md" in _bundle_names(result.path)
    entry = next(p for p in list_profiles(home) if p["id"] == "default")
    assert entry["suggested_name"] == "my-agent"


def test_create_defaults_to_the_profile_you_run_as(home: Path):
    from agent_templates.profiles import current_profile

    assert current_profile(home / "profiles" / "testbot") == "testbot"
    assert current_profile(home) == "default"


def test_nothing_private_in_a_bundle_even_with_a_heavy_profile(home: Path):
    """The native export walks everything; a template must stay small and clean."""
    profile = home / "profiles" / "testbot"
    for sub in ("workspace", "logs", "cache", "image_cache", "sessions"):
        (profile / sub).mkdir(parents=True, exist_ok=True)
        for i in range(50):
            (profile / sub / f"f{i}.txt").write_text("x" * 1000, encoding="utf-8")
    (profile / "state.db").write_bytes(b"\0" * 200_000)
    result = build(home, "testbot")
    names = _bundle_names(result.path)
    assert not any(n.split("/")[0] in {"workspace", "logs", "cache", "image_cache", "sessions"} for n in names)
    assert "state.db" not in names
    assert result.bytes < 50_000
