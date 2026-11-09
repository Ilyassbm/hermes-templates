# Agent Templates — one-click share and install of a whole Hermes agent

`hermes-templates` packages an agent as a **recipe** and installs it in one
click. Inspired by Grok Bot templates: identity, skills, routines, memory and
plugin ids travel; credentials, sessions and history never do.

```
Share  → pick a profile → choose what travels → .htemplate  → send it (file or link)
Install→ paste the link, drop the file → preview → one click → new profile
```

## Install

```bash
hermes plugins install /path/to/hermes-templates --enable   # or: hermes plugins enable hermes-templates
hermes template list
```

Desktop: **Templates** appears in the sidebar (`/templates`).

## CLI

```bash
hermes template list
hermes template create <profile> [-o out.htemplate] [--with-memories] [--skill NAME]…
hermes template inspect <file-or-https-link>
hermes template install <file-or-https-link> [--name NAME] [--force] [-y]
```

## What travels

| Travels | Never travels |
| --- | --- |
| `SOUL.md` (identity) | `.env`, `auth.json`, API keys, OAuth tokens |
| `config.yaml` (accounts and endpoints stripped) | platform sessions, cookies, browser profiles |
| `skills/` (credentials inside them excluded) | conversation history, `state.db`, `sessions/` |
| `cron/jobs.json` — installed **paused** | logs, caches, `workspace/`, `home/` |
| `mcp.json` — env values become keys to reconnect | plugin code, custom scripts |
| `memories/` — **opt-in only** | anything under `.ssh`, `.aws`, `.gnupg`, `.kube`… |
| plugin ids | |

Every text file is passed through Hermes' own secret redactor
(`agent.redact.redact_sensitive_text`) before it is written into the bundle, and
`config.yaml` also loses `platforms`, `messaging`, `gateway`, `base_url`,
`api_mode` and `model.provider`.

## Why a bundle and not a profile export

`hermes profile export` is a **snapshot**: it can carry memories, sessions and
`state.db`. A template is curated, indexed and refusable. `hermes profile
install` (git distributions) stays the right tool when the agent is a product
you will keep updating; a template is the "send it now, they install it in one
click" path.

## Safety model

- **Pack side** — the path rule refuses credentials, sessions, caches and
  workspaces at any depth (including inside a skill); the redactor runs on every
  text file; the config sanitizer drops accounts and endpoints.
- **Install side** — the archive is inspected without extracting: no absolute
  paths, no `..`, no symlinks, ≤ 2000 files, ≤ 120 MB unpacked, compression-ratio
  guard against zip bombs, only known top-level entries, and every file must
  match its SHA-256 in `template.yaml`. A refused bundle leaves nothing behind.
- **After install** — routines are paused, plugins are only enabled when they
  exist here, `.env.EXAMPLE` lists the keys to reconnect, and a profile that was
  not created from a template is never overwritten.

## Backend routes (`/api/plugins/hermes-templates`)

| Route | What it does |
| --- | --- |
| `GET /profiles` | profiles in this home, with skill/routine/plugin counts |
| `GET /library` | templates built here whose bundle is still on disk |
| `GET /file/<id>` | the bundle, as a download |
| `POST /download/<id>` | the bundle as base64 (the page builds a Blob) |
| `POST /create` | pack a profile |
| `POST /preview` | inspect a link, a path, or uploaded bytes |
| `POST /install` | install after a preview |

The page may read a bundle only from `$HERMES_HOME` or the user's home
directory; the CLI has no such limit (the person typing owns the file).

## Tests

```bash
python -m pytest -q tests/          # 17 tests: packing, redaction, traversal, tamper, collisions
hermes plugins validate .
node --check desktop/plugin.js
```

## Layout

```
__init__.py              plugin entry: CLI command + skill
template_cli.py          `hermes template …`
agent_templates/         packer, installer, safety rules, manifest, store
dashboard/plugin_api.py  REST namespace for the page
desktop/plugin.js        Templates page (Share + Install), no build step
skills/agent-templates/  what a bot reads before using it
tests/                   pytest suite
```
