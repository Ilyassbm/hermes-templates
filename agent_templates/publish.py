"""Publish a template bundle to GitHub and hand back a link that installs it.

The link is the product: ``https://raw.githubusercontent.com/<you>/agent-templates/<branch>/<name>/<name>.htemplate``.
Anyone pastes it in Hermes (Templates ▸ Install, or ``hermes template install <link>``),
sees the preview, and installs. Republishing the same name overwrites the file at
the *same* link, so people who installed from it get "update available" next time.

Rules this module holds to:

* **Nothing leaves without a plan the user saw.** :func:`plan` is read-only and says
  exactly what will be written where (and whether it creates a repository);
  :func:`publish` refuses to create a repository unless told the user confirmed.
* **The bundle is re-verified first.** A file that does not pass the same checks an
  installer runs (credential paths, checksums…) is never uploaded.
* **The token is resolved through Hermes' secret scope** (``agent.secret_scope``),
  used for the GitHub API calls and nowhere else: not logged, not stored, not
  returned, not put in an error message.
* **The link is proven before it is handed out**: the commit-pinned URL is downloaded
  back through the same guarded fetcher an installer uses and compared byte for byte.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .errors import TemplateError
from . import settings
from .install import Bundle, fetch, open_bundle

API = "https://api.github.com"
DEFAULT_REPO = "agent-templates"
PLUGIN_REPO = "Ilyassbm/hermes-templates"
PLUGIN_INSTALL_LINK = f"hermes://plugin/install?repo={PLUGIN_REPO}&enable=1"

# GitHub needs a beat before a just-created repository accepts contents writes.
CREATE_SETTLE_SECONDS = 1.0

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class PublishError(TemplateError):
    """Publishing was refused or failed; the message says what to do about it."""


# ------------------------------------------------------------------ token

def github_token() -> str | None:
    """The GitHub token for this profile, via Hermes' secret scope. Never logged."""
    try:
        from agent.secret_scope import get_secret

        try:
            value = get_secret("GITHUB_TOKEN")
        except Exception:  # e.g. UnscopedSecretError while multiplexing: do NOT fall back to os.environ
            return None
    except ImportError:  # outside a Hermes runtime (unit tests)
        value = os.environ.get("GITHUB_TOKEN")
    value = (value or "").strip()
    return value or None


class GitHub:
    """A very small GitHub REST client. The token only ever goes in the Authorization header."""

    def __init__(self, token: str, api: str | None = None, timeout: int = 30):
        self._token = token
        self.api = (api or API).rstrip("/")
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.api}{path}", data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "hermes-templates/1")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw) if raw else {}
            except ValueError:
                return exc.code, {"message": raw[:200]}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # exc may mention the host, never the headers: safe to surface
            raise PublishError(f"could not reach GitHub: {getattr(exc, 'reason', exc)}") from exc


# ------------------------------------------------------------------ plan

def _split_repo(repo: str, login: str) -> tuple[str, str]:
    # explicit argument > the settings page (Settings → Plugins) > built-in default
    repo = (repo or settings.publish_repo() or DEFAULT_REPO).strip()
    owner, _, name = repo.rpartition("/")
    owner = owner or login
    if not _REPO_RE.match(owner) or not _REPO_RE.match(name):
        raise PublishError(f"invalid repository name {repo!r}")
    return owner, name


def raw_url(owner: str, repo: str, ref: str, template: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{template}/{template}.htemplate"


def plan(bundle_path: Path, *, repo: str = "", token: str | None = None, api: str | None = None) -> dict:
    """Read-only: what publishing this bundle would do. Never writes anything."""
    api = api or API
    bundle = open_bundle(Path(bundle_path))  # same checks as an installer
    manifest = bundle.manifest
    name = manifest["name"]
    token = token or github_token()
    out: dict = {
        "ready": False,
        "problems": [],
        "template": name,
        "version": manifest["version"],
        "title": manifest["title"],
        "files": len(bundle.names),
        "bytes": bundle.bytes,
        "needs_token": False,
    }
    if not token:
        out["needs_token"] = True
        out["problems"].append(
            "No GitHub token. Run `hermes config set GITHUB_TOKEN <token>` — a fine-grained token "
            "with Contents: read & write on your templates repository is enough."
        )
        return out

    client = GitHub(token, api)
    status, me = client.call("GET", "/user")
    if status == 401:
        out["problems"].append("GitHub rejected the token (expired or revoked). Set a new GITHUB_TOKEN.")
        return out
    if status != 200:
        out["problems"].append(f"GitHub answered {status} when checking the token.")
        return out
    login = str(me.get("login") or "")

    owner, repo_name = _split_repo(repo, login)
    out.update({"login": login, "owner": owner, "repo": repo_name, "repo_full": f"{owner}/{repo_name}",
                "path": f"{name}/{name}.htemplate"})

    status, info = client.call("GET", f"/repos/{owner}/{repo_name}")
    if status == 200:
        out["repo_exists"] = True
        out["private"] = bool(info.get("private"))
        out["branch"] = str(info.get("default_branch") or "main")
        permissions = info.get("permissions") or {}
        if permissions and not permissions.get("push"):
            out["problems"].append(f"Your token cannot write to {owner}/{repo_name}.")
        if out["private"]:
            out["problems"].append(
                f"{owner}/{repo_name} is private, so nobody else could open the link. "
                "Make it public, or publish to another repository."
            )
        status, existing = client.call("GET", f"/repos/{owner}/{repo_name}/contents/{out['path']}?ref={out['branch']}")
        out["overwrites"] = status == 200
        out["existing_sha"] = existing.get("sha") if status == 200 else None
    elif status == 404:
        out["repo_exists"] = False
        out["creates_repo"] = True
        out["private"] = False
        out["branch"] = "main"
        out["overwrites"] = False
        if owner != login:
            out["problems"].append(f"{owner}/{repo_name} does not exist and can only be created under {login}.")
    else:
        out["problems"].append(f"GitHub answered {status} when looking up {owner}/{repo_name}.")

    out["url"] = raw_url(owner, repo_name, out.get("branch", "main"), name)
    out["page"] = f"https://github.com/{owner}/{repo_name}/tree/{out.get('branch', 'main')}/{name}"
    out["ready"] = not out["problems"]
    return out


# ------------------------------------------------------------------ publish

def _put(client: GitHub, owner: str, repo: str, path: str, content: bytes, branch: str,
         message: str, sha: str | None = None) -> str:
    body = {"message": message, "content": base64.b64encode(content).decode(), "branch": branch}
    if sha:
        body["sha"] = sha
    status, result = client.call("PUT", f"/repos/{owner}/{repo}/contents/{path}", body)
    if status in (200, 201):
        return str((result.get("commit") or {}).get("sha") or "")
    if status in (403, 404):
        raise PublishError(
            f"GitHub refused the write to {owner}/{repo} ({status}). The token needs Contents: read & write on that repository."
        )
    if status == 409 or status == 422:
        raise PublishError("GitHub reports a conflicting edit; run publish again.")
    raise PublishError(f"GitHub answered {status} while writing {path}.")


def share_text(title: str, url: str) -> str:
    return (
        f"Install “{title}” in Hermes:\n"
        f"  hermes template install {url}\n"
        f"or Hermes Desktop ▸ Templates ▸ Install ▸ paste:\n"
        f"  {url}\n\n"
        f"Needs the Agent Templates plugin (one click in Hermes Desktop):\n"
        f"  {PLUGIN_INSTALL_LINK}"
    )


def publish(
    bundle_path: Path,
    *,
    repo: str = "",
    create_repo: bool = False,
    token: str | None = None,
    api: str | None = None,
    verify: bool = True,
    home: Path | None = None,
) -> dict:
    """Upload the bundle and return the link. Raises PublishError with the next step on any refusal."""
    api = api or API
    p = plan(bundle_path, repo=repo, token=token, api=api)
    if p["needs_token"] or not p.get("login"):
        raise PublishError(p["problems"][0] if p["problems"] else "cannot publish")
    if p["problems"]:
        raise PublishError(" ".join(p["problems"]))
    if p.get("creates_repo") and not create_repo:
        raise PublishError(
            f"{p['repo_full']} does not exist yet. Publishing creates it as a public repository — confirm to continue."
        )

    token = token or github_token()
    client = GitHub(token or "", api)
    owner, repo_name, name = p["owner"], p["repo"], p["template"]
    bundle = open_bundle(Path(bundle_path))
    raw = Path(bundle_path).read_bytes()

    created = False
    if p.get("creates_repo"):
        status, made = client.call("POST", "/user/repos", {
            "name": repo_name,
            "description": "Hermes agent templates — install any of them with `hermes template install <link>`.",
            "private": False,
            "has_issues": False,
            "has_wiki": False,
            "has_projects": False,
            "auto_init": True,
        })
        if status not in (200, 201):
            raise PublishError(
                f"GitHub would not let this token create {owner}/{repo_name} ({status}). "
                f"Create it once on github.com (public, add a README), then publish again."
            )
        created = True
        p["branch"] = str(made.get("default_branch") or "main")
        time.sleep(CREATE_SETTLE_SECONDS)

    branch = p["branch"]
    existing_sha = p.get("existing_sha")
    title, version = bundle.manifest["title"], bundle.manifest["version"]

    readme = _repo_readme(bundle, raw_url(owner, repo_name, branch, name))
    status, current = client.call("GET", f"/repos/{owner}/{repo_name}/contents/{name}/README.md?ref={branch}")
    _put(client, owner, repo_name, f"{name}/README.md", readme.encode(), branch,
         f"{name}: describe v{version}", current.get("sha") if status == 200 else None)

    commit = _put(client, owner, repo_name, p["path"], raw, branch, f"Publish {name} v{version}", existing_sha)

    url = raw_url(owner, repo_name, branch, name)
    pinned = raw_url(owner, repo_name, commit, name) if commit else url
    result = {
        "url": url,
        "pinned_url": pinned,
        "page": p["page"],
        "repo_full": p["repo_full"],
        "created_repo": created,
        "updated": bool(existing_sha),
        "commit": commit,
        "template": name,
        "version": version,
        "title": title,
        "bytes": len(raw),
        "verified": False,
        "share_text": share_text(title, url),
        # Measured on raw.githubusercontent.com: `cache-control: max-age=300`, and the cache key
        # varies on Accept-Encoding, so each kind of client has its own copy that expires on its
        # own clock (a request without that header kept the old file for the full 5 minutes while
        # curl already saw the new one). A query string does not bypass it. The commit-pinned
        # link is immutable and immediate.
        "propagation_note": (
            "GitHub caches this link for up to 5 minutes, separately for each kind of client, so someone "
            "opening it right now may still get the previous version. The commit-pinned link is immediate."
            if existing_sha else ""
        ),
    }
    if verify:
        if home is None:
            raise PublishError("internal error: verification needs a Hermes home for its download cache")
        result["verified"] = _prove(pinned, raw, home)
        if not result["verified"]:
            raise PublishError(
                f"Uploaded, but the link did not return the same file when fetched back ({pinned}). "
                "GitHub may still be propagating it: try the link again in a minute."
            )
    return result


def _prove(url: str, expected: bytes, home: Path) -> bool:
    """Fetch the commit-pinned link back through the installer's own fetcher; compare bytes."""
    want = hashlib.sha256(expected).hexdigest()
    for attempt in range(4):
        try:
            got = fetch(url, home, restricted=True)
            try:
                if hashlib.sha256(got.read_bytes()).hexdigest() == want:
                    return True
            finally:
                got.unlink(missing_ok=True)
        except TemplateError:
            pass
        time.sleep(1.5 * (attempt + 1))
    return False


def _repo_readme(bundle: Bundle, url: str) -> str:
    m = bundle.manifest
    c = m["contents"]
    lines = [f"# {m['title']}", ""]
    if m["description"]:
        lines += [m["description"], ""]
    lines += [
        f"`{m['name']}` · v{m['version']}" + (f" · {m['author']}" if m["author"] else "")
        + (f" · {m['license']}" if m["license"] else ""),
        "",
        "## Install",
        "",
        "```bash",
        f"hermes template install {url}",
        "```",
        "",
        "Or Hermes Desktop ▸ Templates ▸ Install, paste the link, review the preview.",
        f"Needs the Agent Templates plugin: `{PLUGIN_INSTALL_LINK}`",
        "",
        "## What is inside",
        "",
        f"- Identity: {'SOUL.md' if c['soul'] else 'none'}",
        f"- Skills: {', '.join(c['skills']) or 'none'}",
        f"- Routines: {len(c['cron'])} (install paused)",
        f"- MCP servers: {', '.join(c['mcp']) or 'none'}",
        f"- Plugins (ids only): {', '.join(c['plugins']) or 'none'}",
        f"- Memories: {'included' if c['memories'] else 'not included'}",
        "",
        "No credentials, sessions or conversation history are in the bundle.",
        "",
    ]
    return "\n".join(lines)