"""Link tests: fetching from a URL safely, publishing to (a fake) GitHub, updates by link."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agent_templates import api, publish as publisher, store
from agent_templates.errors import InvalidBundle, ProfileExists, TemplateError, UnsafeBundle
from agent_templates.install import fetch, open_bundle, preview, version_key
from agent_templates.netsafe import UnsafeUrl, _GuardedRedirects, _host_is_public, check_url
from agent_templates.pack import build

TOKEN = "unit-test-token-not-a-secret-0001"


# ---------------------------------------------------------------- fixtures

@pytest.fixture()
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    profile = root / "profiles" / "testbot"
    (profile / "skills" / "alpha").mkdir(parents=True)
    (profile / "config.yaml").write_text("model:\n  default: x\n", encoding="utf-8")
    (profile / "SOUL.md").write_text("# Test bot\n", encoding="utf-8")
    (profile / "skills" / "alpha" / "SKILL.md").write_text("# alpha\n", encoding="utf-8")
    return root


class FakeGitHub:
    """Just enough of the GitHub REST API: user, repos, contents."""

    def __init__(self, login="tester", repos=None, can_create=True, push=True, private=False, token=TOKEN):
        self.login, self.token = login, token
        self.repos = dict(repos or {})
        self.can_create, self.push, self.private = can_create, push, private
        self.files: dict[tuple[str, str], dict] = {}
        self.requests: list[dict] = []
        self._n = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def _route(self, method):
                path = self.path.split("?")[0]
                body = self._body() if method in ("POST", "PUT") else {}
                outer.requests.append({"method": method, "path": path, "auth": self.headers.get("Authorization"), "body": body})
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    return self._send(401, {"message": "Bad credentials"})
                if method == "GET" and path == "/user":
                    return self._send(200, {"login": outer.login, "id": 1})
                m = re.match(r"^/repos/([^/]+)/([^/]+)$", path)
                if method == "GET" and m:
                    key = f"{m.group(1)}/{m.group(2)}"
                    if key not in outer.repos:
                        return self._send(404, {"message": "Not Found"})
                    return self._send(200, {"private": outer.repos[key].get("private", outer.private),
                                            "default_branch": "main", "permissions": {"push": outer.push}})
                if method == "POST" and path == "/user/repos":
                    if not outer.can_create:
                        return self._send(403, {"message": "Resource not accessible by personal access token"})
                    outer.repos[f"{outer.login}/{body['name']}"] = {"private": body.get("private", False)}
                    return self._send(201, {"default_branch": "main", "private": body.get("private", False)})
                m = re.match(r"^/repos/([^/]+)/([^/]+)/contents/(.+)$", path)
                if m:
                    key = (f"{m.group(1)}/{m.group(2)}", m.group(3))
                    if method == "GET":
                        if key in outer.files:
                            return self._send(200, {"sha": outer.files[key]["sha"]})
                        return self._send(404, {"message": "Not Found"})
                    if method == "PUT":
                        if not outer.push:
                            return self._send(403, {"message": "forbidden"})
                        existing = outer.files.get(key)
                        if existing and body.get("sha") != existing["sha"]:
                            return self._send(422, {"message": "sha mismatch"})
                        outer._n += 1
                        sha = hashlib.sha1(f"{key}{outer._n}".encode()).hexdigest()
                        outer.files[key] = {"sha": sha, "content": base64.b64decode(body["content"])}
                        return self._send(200 if existing else 201, {"commit": {"sha": f"c{outer._n:039d}"}})
                return self._send(404, {"message": "unrouted " + path})

            def do_GET(self): self._route("GET")
            def do_POST(self): self._route("POST")
            def do_PUT(self): self._route("PUT")

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.api = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def writes(self):
        return [r for r in self.requests if r["method"] in ("POST", "PUT")]

    def close(self):
        self.server.shutdown()


@pytest.fixture()
def gh():
    fake = FakeGitHub()
    yield fake
    fake.close()


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(publisher, "CREATE_SETTLE_SECONDS", 0)


@pytest.fixture()
def bundle(home: Path) -> Path:
    return build(home, "testbot", version="1.0.0").path


def serve(directory: Path):
    """A throwaway local web server for a folder of bundles."""
    import functools
    import http.server

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


# ---------------------------------------------------------------- the link is guarded

@pytest.mark.parametrize("url", [
    "http://example.com/x.htemplate",          # not https (page)
    "ftp://example.com/x.htemplate",
    "file:///etc/passwd",
    "https://user:pw@example.com/x",           # credentials in the link
    "https://127.0.0.1/x", "https://localhost/x", "https://[::1]/x",
    "https://10.0.0.5/x", "https://192.168.1.2/x", "https://169.254.169.254/latest/meta-data/",
    "https://0.0.0.0/x", "https://metadata.google.internal/x",
])
def test_the_page_refuses_unsafe_links(url):
    with pytest.raises(UnsafeUrl):
        check_url(url, restricted=True)


def test_the_cli_allows_a_private_host_but_not_a_weird_scheme():
    assert check_url("http://127.0.0.1:8000/x.htemplate", restricted=False)
    with pytest.raises(UnsafeUrl):
        check_url("file:///etc/passwd", restricted=False)
    with pytest.raises(UnsafeUrl):
        check_url("https://user:pw@example.com/x", restricted=False)


def test_host_check_uses_every_dns_answer(monkeypatch):
    import socket

    def answers(*ips):
        return lambda host, port: [(socket.AF_INET, 1, 6, "", (ip, 0)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", answers("93.184.216.34"))
    assert _host_is_public("example.com") is True
    monkeypatch.setattr(socket, "getaddrinfo", answers("93.184.216.34", "10.0.0.1"))
    assert _host_is_public("example.com") is False  # one private answer is enough to refuse
    monkeypatch.setattr(socket, "getaddrinfo", answers("::ffff:127.0.0.1"))
    assert _host_is_public("example.com") is False


def test_a_redirect_cannot_reach_an_internal_address_or_downgrade():
    import urllib.request

    guard = _GuardedRedirects(restricted=True)
    req = urllib.request.Request("https://example.com/a")
    with pytest.raises(UnsafeUrl):
        guard.redirect_request(req, None, 302, "Found", {}, "https://169.254.169.254/")
    with pytest.raises(UnsafeUrl):
        _GuardedRedirects(restricted=False).redirect_request(req, None, 302, "Found", {}, "http://example.com/b")
    capped = _GuardedRedirects(restricted=False)
    capped.hops = 3
    with pytest.raises(UnsafeUrl):
        capped.redirect_request(req, None, 302, "Found", {}, "https://example.com/c")


def test_the_page_cannot_fetch_from_a_local_server(home: Path, bundle: Path):
    server, base = serve(bundle.parent)
    try:
        with pytest.raises(UnsafeUrl):
            api.inspect(home, f"{base}/{bundle.name}", restricted=True)
        assert not list((home / "plugin-data" / "hermes-templates").glob("downloads/*"))  # nothing was fetched
    finally:
        server.shutdown()


def test_download_is_capped_whatever_the_server_says(home: Path, tmp_path: Path, monkeypatch):
    from agent_templates import install as install_mod

    big = tmp_path / "srv"
    big.mkdir()
    (big / "big.htemplate").write_bytes(b"0" * 200_000)
    monkeypatch.setattr(install_mod, "MAX_BUNDLE_BYTES", 50_000)
    server, base = serve(big)
    try:
        with pytest.raises(TemplateError):
            fetch(f"{base}/big.htemplate", home)
        assert not list((home / "plugin-data" / "hermes-templates" / "downloads").glob("*.htemplate"))
    finally:
        server.shutdown()


def test_a_link_to_something_that_is_not_a_bundle_is_refused(home: Path, tmp_path: Path):
    srv = tmp_path / "srv"
    srv.mkdir()
    (srv / "page.htemplate").write_text("<html>not a zip</html>", encoding="utf-8")
    server, base = serve(srv)
    try:
        with pytest.raises(InvalidBundle):
            api.inspect(home, f"{base}/page.htemplate")
    finally:
        server.shutdown()


# ---------------------------------------------------------------- install from a link, update by the same link

def test_install_from_a_link_remembers_it_and_offers_the_update(home: Path):
    out = home.parent / "srv"
    out.mkdir()
    build(home, "testbot", version="1.0.0", out=out / "testbot.htemplate")
    server, base = serve(out)
    link = f"{base}/testbot.htemplate"
    try:
        first = api.apply(home, link, name="copybot")
        marker = json.loads((home / "profiles" / "copybot" / ".hermes-template.json").read_text())
        assert marker["source_url"] == link and first["manifest"]["version"] == "1.0.0"

        # reopening the link finds the profile installed from it: no name to retype
        found = api.inspect(home, link)
        assert found["name"] == "copybot" and found["installed_as"] == "copybot" and found["update"]["same"] is True

        same = api.inspect(home, link, name="copybot")
        assert same["update"]["same"] is True and "Already at v1.0.0" in same["warnings"][0]

        build(home, "testbot", version="1.1.0", out=out / "testbot.htemplate")  # author republishes: same link
        newer = api.inspect(home, link, name="copybot")
        assert newer["update"]["newer"] is True
        assert newer["update"]["from"] == "1.0.0" and newer["update"]["to"] == "1.1.0"
        assert newer["warnings"][0].startswith("Update available: v1.0.0 → v1.1.0")

        with pytest.raises(ProfileExists):
            api.apply(home, link, name="copybot")  # never silently
        updated = api.apply(home, link, name="copybot", force=True)
        assert updated["replaced"] is True and updated["manifest"]["version"] == "1.1.0"
    finally:
        server.shutdown()


def test_versions_compare_as_numbers():
    assert version_key("1.10.0") > version_key("1.9.0")
    assert version_key("2.0.0") > version_key("1.99.99")
    assert version_key("1.0.0") == version_key("1.0.0")


# ---------------------------------------------------------------- publish

def test_plan_without_a_token_writes_nothing(bundle: Path, gh: FakeGitHub, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    plan = publisher.plan(bundle, token=None, api=gh.api)
    assert plan["needs_token"] and not plan["ready"] and "GITHUB_TOKEN" in plan["problems"][0]
    assert gh.requests == []


def test_plan_is_read_only_and_says_it_would_create_the_repo(bundle: Path, gh: FakeGitHub):
    plan = publisher.plan(bundle, token=TOKEN, api=gh.api)
    assert plan["ready"] and plan["creates_repo"] and plan["repo_full"] == "tester/agent-templates"
    assert plan["url"] == "https://raw.githubusercontent.com/tester/agent-templates/main/testbot/testbot.htemplate"
    assert gh.writes() == []


def test_publish_refuses_to_create_a_repo_unless_confirmed(bundle: Path, gh: FakeGitHub):
    with pytest.raises(publisher.PublishError) as exc:
        publisher.publish(bundle, token=TOKEN, api=gh.api, verify=False)
    assert "confirm" in str(exc.value) and gh.writes() == []


def test_publish_creates_the_repo_and_uploads_exactly_the_bundle(home: Path, bundle: Path, gh: FakeGitHub):
    result = publisher.publish(bundle, create_repo=True, token=TOKEN, api=gh.api, verify=False, home=home)
    assert result["created_repo"] and not result["updated"]
    assert result["url"] == "https://raw.githubusercontent.com/tester/agent-templates/main/testbot/testbot.htemplate"
    assert result["pinned_url"].startswith("https://raw.githubusercontent.com/tester/agent-templates/c")
    created = [r for r in gh.requests if r["path"] == "/user/repos"][0]["body"]
    assert created["private"] is False and created["auto_init"] is True
    uploaded = gh.files[("tester/agent-templates", "testbot/testbot.htemplate")]["content"]
    assert uploaded == bundle.read_bytes()
    assert ("tester/agent-templates", "testbot/README.md") in gh.files
    assert "hermes template install" in result["share_text"] and "hermes://plugin/install?repo=" in result["share_text"]


def test_republishing_overwrites_at_the_same_link(home: Path, bundle: Path, gh: FakeGitHub):
    first = publisher.publish(bundle, create_repo=True, token=TOKEN, api=gh.api, verify=False, home=home)
    newer = build(home, "testbot", version="1.1.0").path
    second = publisher.publish(newer, token=TOKEN, api=gh.api, verify=False, home=home)
    assert second["updated"] is True and second["url"] == first["url"] and second["version"] == "1.1.0"
    assert first["propagation_note"] == ""  # a first publish has no stale cache to warn about
    assert "5 minutes" in second["propagation_note"] and second["pinned_url"] != second["url"]
    assert gh.files[("tester/agent-templates", "testbot/testbot.htemplate")]["content"] == newer.read_bytes()


def test_the_token_only_ever_travels_in_the_authorization_header(home: Path, bundle: Path, gh: FakeGitHub):
    result = publisher.publish(bundle, create_repo=True, token=TOKEN, api=gh.api, verify=False, home=home)
    assert TOKEN not in json.dumps(result)
    for request in gh.requests:
        assert TOKEN not in json.dumps(request["body"])
        assert TOKEN not in request["path"]
    for blob in gh.files.values():
        assert TOKEN.encode() not in blob["content"]


def test_a_bad_token_is_reported_not_raised(bundle: Path, gh: FakeGitHub):
    plan = publisher.plan(bundle, token="some-other-token", api=gh.api)
    assert not plan["ready"] and "rejected the token" in plan["problems"][0]
    assert gh.writes() == []


def test_a_private_repository_is_refused_because_nobody_could_open_the_link(bundle: Path):
    fake = FakeGitHub(repos={"tester/agent-templates": {"private": True}})
    try:
        plan = publisher.plan(bundle, token=TOKEN, api=fake.api)
        assert not plan["ready"] and "private" in plan["problems"][0]
        with pytest.raises(publisher.PublishError):
            publisher.publish(bundle, token=TOKEN, api=fake.api, verify=False, home=Path("."))
        assert fake.writes() == []
    finally:
        fake.close()


def test_a_token_that_cannot_write_is_refused_before_any_write(bundle: Path):
    fake = FakeGitHub(repos={"tester/agent-templates": {}}, push=False)
    try:
        plan = publisher.plan(bundle, token=TOKEN, api=fake.api)
        assert not plan["ready"] and "cannot write" in plan["problems"][0]
        assert fake.writes() == []
    finally:
        fake.close()


def test_a_token_that_cannot_create_repos_gets_the_manual_step(home: Path, bundle: Path):
    fake = FakeGitHub(can_create=False)
    try:
        with pytest.raises(publisher.PublishError) as exc:
            publisher.publish(bundle, create_repo=True, token=TOKEN, api=fake.api, verify=False, home=home)
        assert "Create it once on github.com" in str(exc.value)
    finally:
        fake.close()


def test_a_bundle_carrying_a_credential_is_never_uploaded(tmp_path: Path, gh: FakeGitHub):
    leaky = tmp_path / "leaky.htemplate"
    with zipfile.ZipFile(leaky, "w") as z:
        z.writestr("template.yaml", "schema: hermes-template/1\nname: leaky\n")
        z.writestr(".env", "OPENAI_API_KEY=sk-live\n")
    with pytest.raises(UnsafeBundle):
        publisher.plan(leaky, token=TOKEN, api=gh.api)
    assert gh.requests == []  # refused before the network was even touched


def test_the_link_is_proven_by_fetching_it_back(home: Path, bundle: Path, gh: FakeGitHub, monkeypatch):
    seen = {}

    def good(url, home, **kw):
        seen["restricted"] = kw.get("restricted")
        copy = home / "back.htemplate"
        copy.write_bytes(bundle.read_bytes())
        return copy

    monkeypatch.setattr(publisher, "fetch", good)
    result = publisher.publish(bundle, create_repo=True, token=TOKEN, api=gh.api, home=home)
    assert result["verified"] is True and seen["restricted"] is True  # same guarded fetcher an installer uses

    def bad(url, home, **kw):
        copy = home / "back2.htemplate"
        copy.write_bytes(b"something else")
        return copy

    monkeypatch.setattr(publisher, "fetch", bad)
    monkeypatch.setattr(publisher.time, "sleep", lambda s: None)
    with pytest.raises(publisher.PublishError) as exc:
        publisher.publish(bundle, token=TOKEN, api=gh.api, home=home)
    assert "did not return the same file" in str(exc.value)


def test_api_records_the_link_next_to_the_template(home: Path, gh: FakeGitHub, monkeypatch):
    monkeypatch.setattr(publisher, "github_token", lambda: TOKEN)
    monkeypatch.setattr(publisher, "API", gh.api)

    def back(url, home, **kw):  # the "download it back" step, served from the library
        out = home / "back.htemplate"
        out.write_bytes(Path(store.library(home)[0]["path"]).read_bytes())
        return out

    monkeypatch.setattr(publisher, "fetch", back)
    created = api.create(home, "testbot", version="1.0.0")
    result = api.publish(home, created["id"], create_repo=True)
    assert result["verified"] is True
    entry = store.find(home, created["id"])
    assert entry["published"]["url"] == result["url"] and entry["published"]["version"] == "1.0.0"

def test_a_link_never_resolves_to_a_profile_installed_from_another_link(home: Path):
    out = home.parent / "srv"
    out.mkdir()
    build(home, "testbot", version="1.0.0", out=out / "testbot.htemplate")
    server, base = serve(out)
    try:
        api.apply(home, f"{base}/testbot.htemplate", name="copybot")
        other = api.inspect(home, f"{base}/testbot.htemplate?other=1")
        assert other["installed_as"] == "" and other["name"] == "testbot"
    finally:
        server.shutdown()


# ---------------------------------------------------------------- settings page

def test_the_settings_page_supplies_the_default_repository(monkeypatch):
    from agent_templates import publish as pub, settings as st

    monkeypatch.setattr(st, "publish_repo", lambda: "someone/their-templates")
    assert pub._split_repo("", "login") == ("someone", "their-templates")
    # an explicit argument still wins over the setting
    assert pub._split_repo("me/mine", "login") == ("me", "mine")


def test_an_empty_setting_falls_back_to_the_builtin_default(monkeypatch):
    from agent_templates import publish as pub, settings as st

    monkeypatch.setattr(st, "publish_repo", lambda: "")
    assert pub._split_repo("", "login") == ("login", pub.DEFAULT_REPO)


def test_the_bound_context_is_how_the_setting_is_read():
    from agent_templates import settings as st

    class Ctx:
        def get_config(self, key, default=None):
            return {"publish_repo": "team/templates"}.get(key, default)

    st.bind(Ctx())
    assert st.publish_repo() == "team/templates"
    st.bind(None)                      # an unbound plugin falls back to the default
    assert st.publish_repo() == ""


def test_a_context_that_raises_is_not_fatal():
    from agent_templates import settings as st

    class Broken:
        def get_config(self, key, default=None):
            raise RuntimeError("no config here")

    st.bind(Broken())
    assert st.publish_repo() == ""
    st.bind(None)


def test_a_broken_config_never_breaks_publishing(monkeypatch):
    from agent_templates import settings as st

    monkeypatch.setattr(st, "_settings", lambda: {"publish_repo": 42})
    assert st.publish_repo() == ""  # not a string -> ignore it, no exception


# ---------------------------------------------------------------- ?profile= and credentials

def test_a_named_profile_resolves_only_when_it_really_exists(home: Path):
    from agent_templates.profiles import profile_dir_for

    (home / "profiles" / "other").mkdir()
    assert profile_dir_for(home, "testbot") == home / "profiles" / "testbot"
    assert profile_dir_for(home, "other") == home / "profiles" / "other"
    for bad in ("", "  ", "../testbot", "testbot/../other", ".deleted", "missing", "/etc"):
        assert profile_dir_for(home, bad) is None, bad


def test_a_tombstoned_profile_is_not_a_credential_source(home: Path):
    from agent_templates.profiles import profile_dir_for

    (home / "profiles" / ".deleted").mkdir()
    (home / "profiles" / ".deleted" / "gone").write_text("deleted\n")
    (home / "profiles" / "gone").mkdir()
    assert profile_dir_for(home, "gone") is None
