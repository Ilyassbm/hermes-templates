"""``hermes template`` — create, inspect and install agent templates.

The CLI is the same engine the desktop page drives: same packer, same checks,
same refusals. Anything the page can do, a script can do, and the other way
round.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_templates import __version__, api, store
from agent_templates.errors import TemplateError
from agent_templates.profiles import current_profile, hermes_home, list_profiles

HELP = "Share a whole agent as a template, or install one"
DESCRIPTION = (
    "A template is a recipe of an agent — identity (SOUL.md), configuration, skills, routines, "
    "MCP servers, memories the author publishes, and plugin ids — packaged as one .htemplate file "
    "or link. Credentials, sessions, conversation history, logs and caches never travel, and every "
    "text file is redacted before it is packed. Install always previews first and shows what will run: "
    "routines arrive paused, MCP servers arrive disabled, the config is sanitized again, and plugins this "
    "machine already has are enabled (pass --no-enable-plugins to keep them off)."
)


def setup_cli(parser) -> None:
    sub = parser.add_subparsers(dest="template_action")

    listing = sub.add_parser("list", help="Profiles you can share, and templates already built")
    listing.add_argument("--json", action="store_true", help="Machine-readable output")

    create = sub.add_parser("create", help="Pack a profile into a .htemplate bundle")
    create.add_argument("profile", nargs="?", default=None,
                        help="Profile to pack (default: the profile you are running as)")
    create.add_argument("--out", help="Output file (default: plugin-data/hermes-templates/)")
    create.add_argument("--name", help="Template id (default: the profile name)")
    create.add_argument("--title", help="Display name")
    create.add_argument("--description", help="One line about what this agent does")
    create.add_argument("--author", default="", help="Author name")
    create.add_argument("--license", default="", help="License (MIT, CC-BY-4.0…)")
    create.add_argument("--template-version", default="1.0.0", help="Template version (default 1.0.0)")
    create.add_argument("--no-skills", action="store_true", help="Leave skills out")
    create.add_argument("--skill", action="append", metavar="NAME", help="Only this skill (repeatable)")
    create.add_argument("--no-cron", action="store_true", help="Leave routines out")
    create.add_argument("--no-mcp", action="store_true", help="Leave MCP servers out")
    create.add_argument("--no-plugins", action="store_true", help="Do not list plugin ids")
    create.add_argument("--with-memories", action="store_true",
                        help="Include memories/MEMORY.md and USER.md (they are readable by whoever installs)")
    create.add_argument("--json", action="store_true", help="Machine-readable output")

    publish = sub.add_parser(
        "publish", help="Publish a template to GitHub and print the link that installs it")
    publish.add_argument("what", nargs="?", default=None,
                         help="A profile to pack and publish (default: the profile you run as), or a .htemplate file")
    publish.add_argument("--repo", default="", help="GitHub repository, name or owner/name (default: <you>/agent-templates)")
    publish.add_argument("--create-repo", action="store_true",
                         help="Allow creating the repository (public) if it does not exist")
    publish.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation")
    publish.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="Show exactly what a bundle carries, before installing")
    inspect.add_argument("source", help="Bundle path or https:// link")
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--file", metavar="PATH", help="Print one file of the bundle in full (e.g. SOUL.md)")

    install = sub.add_parser("install", help="Install a bundle as a new profile")
    install.add_argument("source", help="Bundle path or https:// link")
    install.add_argument("--name", help="Profile name (default: the template's name)")
    install.add_argument("--force", action="store_true", help="Update a template installed earlier")
    install.add_argument("--no-enable-plugins", action="store_true",
                         help="Do not enable the plugins this machine already has")
    install.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation")
    install.add_argument("--json", action="store_true")


def handle_cli(args) -> int:
    home = hermes_home()
    action = getattr(args, "template_action", None)
    try:
        if action == "list":
            return _list(home, args)
        if action == "create":
            return _create(home, args)
        if action == "publish":
            return _publish(home, args)
        if action == "inspect":
            return _inspect(home, args)
        if action == "install":
            return _install(home, args)
    except TemplateError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print("usage: hermes template {list|create|publish|inspect|install}", file=sys.stderr)
    return 2


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _list(home: Path, args) -> int:
    profiles = list_profiles(home)
    library = store.library(home)
    if args.json:
        print(json.dumps({"home": str(home), "profiles": profiles, "templates": library}, indent=2))
        return 0
    print(f"hermes-templates {__version__} — {home}")
    print("\nAgents you can share:")
    if not profiles:
        print("  (none)")
    for profile in profiles:
        bits = [_plural(profile["skills_count"], "skill"), _plural(profile["cron_count"], "routine")]
        if profile["plugins"]:
            bits.append(_plural(len(profile["plugins"]), "plugin"))
        print(f"  {profile['id']:<20} {' · '.join(bits)}")
    print("\nTemplates built here:")
    if not library:
        print("  (none)")
    for entry in library:
        size = f"{entry['bytes'] / 1024:.0f} KB"
        print(f"  {entry['name']:<20} v{entry['version']:<8} {size:>8}  {entry['path']}")
    return 0


def _create(home: Path, args) -> int:
    result = api.create(
        home,
        args.profile or current_profile(home),
        name=args.name or "",
        title=args.title or "",
        description=args.description or "",
        author=args.author,
        license=args.license,
        version=args.template_version,
        skills=not args.no_skills,
        skill_names=args.skill or None,
        cron=not args.no_cron,
        memories=args.with_memories,
        mcp=not args.no_mcp,
        plugins=not args.no_plugins,
        out=Path(args.out).expanduser() if args.out else None,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    manifest = result["manifest"]
    print(f"✓ {manifest['title']}  ({manifest['name']} v{manifest['version']})")
    print(f"  {result['files']} files · {result['bytes'] / 1024:.0f} KB · {result['redactions']} redaction(s)")
    print(f"  {result['path']}")
    if manifest["contents"]["skills"]:
        print(f"  skills: {', '.join(manifest['contents']['skills'])}")
    if manifest["contents"]["cron"]:
        print(f"  routines: {len(manifest['contents']['cron'])} (installed paused)")
    if result["notes"]:
        for note in result["notes"]:
            print(f"  · {note}")
    if result["excluded"]:
        print(f"  left out: {len(result['excluded'])} path(s), e.g. {result['excluded'][0]}")
    print("\n  Install it with:")
    print(f"    hermes template install {result['path']}")
    return 0


def _inspect(home: Path, args) -> int:
    data = api.inspect(home, args.source)
    if getattr(args, "file", None):
        from agent_templates.peek import view
        from agent_templates.install import open_bundle

        shown = view(open_bundle(Path(data["source"])), args.file)
        if shown["binary"]:
            print(f"{shown['path']}: binary file, {shown['size']} bytes", file=sys.stderr)
            return 1
        print(shown["text"], end="" if shown["text"].endswith("\n") else "\n")
        if shown["truncated"]:
            print(f"… truncated at {len(shown['text'])} of {shown['size']} bytes", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    manifest = data["manifest"]
    print(f"{manifest['title']}  ({manifest['name']} v{manifest['version']})")
    if manifest["description"]:
        print(f"  {manifest['description']}")
    print(f"  author: {manifest['author'] or 'unknown'} · license: {manifest['license'] or 'unspecified'}")
    print(f"  {len(data['files'])} files · {data['bytes'] / 1024:.0f} KB · target profile: {data['name']}")
    contents = manifest["contents"]
    skill_count = len((data.get("readable") or {}).get("skills") or []) or len(contents["skills"])
    print(f"  identity: {'SOUL.md' if contents['soul'] else 'none'} · skills: {skill_count}"
          f" · routines: {len(contents['cron'])} · mcp: {len(contents['mcp'])} · memories: "
          f"{'included' if contents['memories'] else 'no'}")
    if contents["cron"]:
        print("  routines: " + ", ".join(contents["cron"]))
    if contents["plugins"]:
        print(f"  plugins: {', '.join(contents['plugins'])}")
    for item in data["env_requires"]:
        mark = "✓" if item["set"] or item["in_env_file"] else "·"
        print(f"  {mark} {item['name']} — {item['description'] or 'required'}")
    _print_will_run(data)
    for warning in data["warnings"]:
        print(f"  ! {warning}")
    return 0


def _print_will_run(data: dict) -> None:
    """What the bundle would run, printed before the user is asked anything."""
    readable = data.get("readable") or {}
    soul = readable.get("soul")
    if soul and soul.get("text"):
        lines = soul["text"].strip().splitlines()
        print("  SOUL.md:")
        for line in lines[:12]:
            print(f"    | {line}")
        if len(lines) > 12 or soul.get("truncated"):
            print(f"    | … ({len(lines)} lines — read it all with: hermes template inspect <source> --file SOUL.md)")
    for skill in readable.get("skills") or []:
        print(f"  skill {skill['name']}: {skill['description'] or '(no description)'}")
    plan = data.get("will_run") or {}
    for server in plan.get("mcp") or []:
        print(f"  MCP {server['name']} (arrives disabled) would run: {server['line']}")
    for routine in plan.get("routines") or []:
        state = "shipped ENABLED, arrives paused" if routine["ships_enabled"] else "arrives paused"
        print(f"  routine {routine['name']} [{routine['schedule']}] ({state})")
        if routine.get("script"):
            print(f"    script: {routine['script']}")
        if routine.get("prompt"):
            print(f"    prompt: {routine['prompt']}")
    for item in plan.get("risky") or []:
        print(f"  ! sets {item['key']}: {item['why']}")


def _install(home: Path, args) -> int:
    preview = api.inspect(home, args.source, name=args.name or "")
    if not args.json:
        manifest = preview["manifest"]
        print(f"{manifest['title']}  ({manifest['name']} v{manifest['version']})")
        if manifest["description"]:
            print(f"  {manifest['description']}")
        print(f"  → profile {preview['name']!r} at {preview['target_path']}")
        _print_will_run(preview)
        for warning in preview["warnings"]:
            print(f"  ! {warning}")
        if preview.get("plugins_available") and not args.no_enable_plugins:
            print(f"  plugins that will be enabled (already installed here): {', '.join(preview['plugins_available'])}"
                  " — pass --no-enable-plugins to keep them off")
    if not args.yes:
        if not sys.stdin.isatty():
            print("✗ refusing to install without confirmation (pass -y in a script)", file=sys.stderr)
            return 1
        answer = input("Install this template? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "o", "oui"):
            print("cancelled")
            return 1
    result = api.apply(
        home, args.source, name=args.name or "", force=args.force,
        enable_plugins=not args.no_enable_plugins,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    print(f"✓ installed as {result['profile']!r} → {result['path']}")
    if result["plugins_enabled"]:
        print(f"  plugins enabled: {', '.join(result['plugins_enabled'])}")
    if result["plugins_missing"]:
        print(f"  plugins missing here: {', '.join(result['plugins_missing'])}")
    print(f"  start it with: hermes -p {result['profile']} chat")
    for step in result.get("next_steps") or []:
        print(f"  connect a key: {step}")
    return 0

def _publish(home: Path, args) -> int:
    from agent_templates import publish as publisher

    target = args.what
    candidate = Path(target).expanduser() if target else None
    if candidate is not None and candidate.is_file():
        bundle_path, template_id = candidate, ""
    else:
        built = api.create(home, target or current_profile(home))
        bundle_path, template_id = Path(built["path"]), built["id"]
        if not args.json:
            manifest = built["manifest"]
            print(f"✓ packed {manifest['title']} v{manifest['version']} — {built['files']} files, "
                  f"{built['bytes'] / 1024:.0f} KB, {built['redactions']} redaction(s)")

    plan = publisher.plan(bundle_path, repo=args.repo)
    if plan["problems"]:
        for problem in plan["problems"]:
            print(f"✗ {problem}", file=sys.stderr)
        return 1

    if not args.json:
        print(f"\nThis will publish to {plan['repo_full']} (branch {plan['branch']}):")
        print(f"  {plan['path']}  (+ README.md next to it)")
        print(f"  {'overwrites the current version at the same link' if plan['overwrites'] else 'new file'}")
        if plan.get("creates_repo"):
            print(f"  ! {plan['repo_full']} does not exist: it will be created as a PUBLIC repository")
        print(f"  link: {plan['url']}")
        print("  Anyone with the link can read the bundle. It contains no credentials, sessions or history.")

    if plan.get("creates_repo") and not args.create_repo:
        if args.yes or not sys.stdin.isatty():
            print("✗ the repository does not exist; pass --create-repo to allow creating it", file=sys.stderr)
            return 1
        if input(f"Create the public repository {plan['repo_full']}? [y/N] ").strip().lower() not in ("y", "yes", "o", "oui"):
            print("cancelled")
            return 1
        args.create_repo = True
    elif not args.yes:
        if not sys.stdin.isatty():
            print("✗ refusing to publish without confirmation (pass -y in a script)", file=sys.stderr)
            return 1
        if input("Publish? [y/N] ").strip().lower() not in ("y", "yes", "o", "oui"):
            print("cancelled")
            return 1

    result = publisher.publish(bundle_path, repo=args.repo, create_repo=args.create_repo, home=home)
    if template_id:
        store.record_publication(home, template_id, result)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"\n✓ published {result['title']} v{result['version']}"
          f"{' (repository created)' if result['created_repo'] else ''}"
          f"{' — updated at the same link' if result['updated'] else ''}")
    print(f"  link verified: {'yes' if result['verified'] else 'NO'}")
    if result.get("propagation_note"):
        print(f"  ! {result['propagation_note']}")
        print(f"    immediate: {result['pinned_url']}")
    print(f"\n{result['url']}\n")
    print("Share this:\n")
    print("  " + result["share_text"].replace("\n", "\n  "))
    return 0