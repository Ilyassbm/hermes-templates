---
name: agent-templates
description: "Use when sharing an agent as a template or installing one from a link (.htemplate)."
version: 1.3.0
author: hermes-templates plugin
license: MIT
metadata:
  hermes:
    tags: [templates, sharing, profiles, distribution]
---

# Agent templates

A template is a **recipe** of an agent, not a clone: identity, configuration,
skills, routines, MCP definitions, published memories, plugin ids. It travels as
one `.htemplate` file (a zip with `template.yaml` at its root) or as any HTTPS
link that serves that file.

## Create one

```bash
hermes template list                 # profiles here + templates already built
hermes template create dex \
  --title "Dex — CTO" --description "Orchestre les livraisons" \
  --author "Ilyass Bm" --license MIT --version 1.0.0
```

Default: everything except memories. `--with-memories` is the only way memories
travel, and it is a publishing decision, not a convenience toggle.
`--skill NAME` (repeatable) narrows the skills; `--no-cron`, `--no-mcp`,
`--no-plugins`, `--no-skills` leave those out.

## Publish a link

```bash
hermes template publish ./dex-1.0.0.htemplate          # plan, then a yes/no
hermes template publish ./dex-1.0.0.htemplate -y       # no question
```

Publishing uploads the bundle to a GitHub repository (`/agent-templates` by
default) and returns
`https://raw.githubusercontent.com/<owner>/agent-templates/main/<name>/<name>.htemplate`.
Publishing again **replaces the file at the same link**, so a link already sent
stays the update channel. The tool downloads the published URL back and compares
bytes before it shows the link; the result says `link verified: yes`.

Needs `GITHUB_TOKEN` with *Contents: read and write* (plus *Administration: write*
if the repository must be created). Read it from the secret store, never type it
into a file:

```bash
hermes config set GITHUB_TOKEN <token>
```

Never create a repository on your own initiative: `--create-repo` (CLI) or the
plan's confirmation (page) is the owner's yes, and the plan says the repository
will be **public**.

After an update, `raw.githubusercontent.com` may keep serving the old file for up
to 5 minutes, per kind of client (measured: `curl` had the new one immediately,
a request without `Accept-Encoding` kept the old one for the full five minutes; a
query string does not bypass it). Hand out the commit-pinned URL when the update
has to land instantly.

## Inspect and install

```bash
hermes template inspect ./dex-1.0.0.htemplate   # SOUL.md, skills, routines, MCP commands — before anything is written
hermes template inspect ./dex-1.0.0.htemplate --file SOUL.md   # one file in full
hermes template install ./dex-1.0.0.htemplate --name dex-copy
hermes template install https://raw.githubusercontent.com/…/dex.htemplate
```

A link is a first-class source: the bundle is downloaded to
`plugin-data/hermes-templates/downloads/`, opened without extracting, and nothing
is written before you confirm. The link is remembered in the installed profile's
`.hermes-template.json`; reopening the same link then reads
`Update available: v1.0.0 → v1.1.0` instead of asking for a new name, and the
update keeps memories, sessions and `.env`.

Install refuses rather than guesses:

- a bundle carrying `.env`, `auth.json`, sessions, caches or any credential path
  is refused, and the refusal names the file;
- every file must match its SHA-256 in `template.yaml`, so a bundle edited after
  it was built is refused;
- routines arrive **paused** through Hermes' cron import, MCP servers arrive
  **disabled**, and the config is sanitized again on install;
- plugins already installed on this machine are enabled (`--no-enable-plugins`
  keeps them off), and the rest are listed as missing;
- an existing profile is never overwritten unless it was installed from a
  template (`--force` updates that one, user data stays).

## What never travels

`.env`, `auth.json`, OAuth and platform sessions, cookies, browser profiles,
conversation history, `state.db`, logs, caches, workspace, `home/`, key material.
Every text file that does travel passes through Hermes' own secret redactor
(`agent.redact.redact_sensitive_text`) before it is written into the bundle.
`config.yaml` additionally loses `platforms`/`messaging`/`gateway`, `base_url`,
`api_mode` and every `api_key`: the provider name stays, its endpoint and
credentials do not — the installer brings their own account.

## Desktop

Templates page (`/templates`): **Share** picks a profile, shows what travels,
packs, and offers the download plus the install command. **Install** takes a
link, a path, or a dropped file, previews it, and installs on confirmation.

## Rules of thumb

- Share a template, never a profile export: an export is a snapshot that can
  carry memories and conversation history.
- Tell the person what to reconnect (the preview lists the env keys).
- A template that needs a plugin they do not have is not broken — it says so.
