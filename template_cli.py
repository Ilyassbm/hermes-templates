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
from agent_templates.profiles import hermes_home, list_profiles

HELP = "Share a whole agent as a template, or install one"
DESCRIPTION = (
    "A template is a recipe of an agent — identity (SOUL.md), configuration, skills, routines, "
    "MCP servers, memories the author publishes, and plugin ids — packaged as one .htemplate file "
    "or link. Credentials, sessions, conversation history, logs and caches never travel, and every "
    "text file is redacted before it is packed. Install always previews first: routines arrive "
    "paused and plugins are never auto-enabled."
)


def setup_cli(parser) -> None:
    sub = parser.add_subparsers(dest="template_action")

    listing = sub.add_parser("list", help="Profiles you can share, and templates already built")
    listing.add_argument("--json", action="store_true", help="Machine-readable output")

    create = sub.add_parser("create", help="Pack a profile into a .htemplate bundle")
    create.add_argument("profile", help="Profile to pack ('default' for this home's own agent)")
    create.add_argument("-o", "--output", help="Output file (default: plugin-data/hermes-templates/)")
    create.add_argument("--name", help="Template id (default: the profile name)")
    create.add_argument("--title", help="Display name")
    create.add_argument("--description", help="One line about what this agent does")
    create.add_argument("--author", default="", help="Author name")
    create.add_argument("--license", default="", help="License (MIT, CC-BY-4.0…)")
    create.add_argument("--version", default="1.0.0", help="Template version")
    create.add_argument("--no-skills", action="store_true", help="Leave skills out")
    create.add_argument("--skill", action="append", metavar="NAME", help="Only this skill (repeatable)")
    create.add_argument("--no-cron", action="store_true", help="Leave routines out")
    create.add_argument("--no-mcp", action="store_true", help="Leave MCP servers out")
    create.add_argument("--no-plugins", action="store_true", help="Do not list plugin ids")
    create.add_argument("--with-memories", action="store_true",
                        help="Include memories/MEMORY.md and USER.md (they are readable by whoever installs)")
    create.add_argument("--json", action="store_true", help="Machine-readable output")

    inspect = sub.add_parser("inspect", help="Show exactly what a bundle carries, before installing")
    inspect.add_argument("source", help="Bundle path or https:// link")
    inspect.add_argument("--json", action="store_true")

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
        if action == "inspect":
            return _inspect(home, args)
        if action == "install":
            return _install(home, args)
    except TemplateError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print("usage: hermes template {list|create|inspect|install}", file=sys.stderr)
    return 2


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
        bits = [f"{profile['skills_count']} skills", f"{profile['cron_count']} routines"]
        if profile["plugins"]:
            bits.append(f"{len(profile['plugins'])} plugins")
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
        args.profile,
        name=args.name or "",
        title=args.title or "",
        description=args.description or "",
        author=args.author,
        license=args.license,
        version=args.version,
        skills=not args.no_skills,
        skill_names=args.skill or None,
        cron=not args.no_cron,
        memories=args.with_memories,
        mcp=not args.no_mcp,
        plugins=not args.no_plugins,
        out=Path(args.output).expanduser() if args.output else None,
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
    print(f"  identity: {'SOUL.md' if contents['soul'] else 'none'} · skills: {len(contents['skills'])}"
          f" · routines: {len(contents['cron'])} · mcp: {len(contents['mcp'])} · memories: "
          f"{'included' if contents['memories'] else 'no'}")
    if contents["skills"]:
        print(f"  skills: {', '.join(contents['skills'])}")
    if contents["cron"]:
        print("  routines: " + ", ".join(contents["cron"]))
    if contents["plugins"]:
        print(f"  plugins: {', '.join(contents['plugins'])}")
    for item in data["env_requires"]:
        mark = "✓" if item["set"] or item["in_env_file"] else "·"
        print(f"  {mark} {item['name']} — {item['description'] or 'required'}")
    for warning in data["warnings"]:
        print(f"  ! {warning}")
    return 0


def _install(home: Path, args) -> int:
    preview = api.inspect(home, args.source, name=args.name or "")
    if not args.json:
        manifest = preview["manifest"]
        print(f"{manifest['title']}  ({manifest['name']} v{manifest['version']})")
        if manifest["description"]:
            print(f"  {manifest['description']}")
        print(f"  → profile {preview['name']!r} at {preview['target_path']}")
        for warning in preview["warnings"]:
            print(f"  ! {warning}")
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
    return 0
