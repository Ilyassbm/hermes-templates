---
name: agent-templates
description: "Use when sharing an agent as a template or installing one from a link (.htemplate)."
version: 1.0.0
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

## Inspect and install

```bash
hermes template inspect ./dex-1.0.0.htemplate   # everything it carries, before anything is written
hermes template install ./dex-1.0.0.htemplate --name dex-copy
```

Install refuses rather than guesses:

- a bundle carrying `.env`, `auth.json`, sessions, caches or any credential path
  is refused, and the refusal names the file;
- every file must match its SHA-256 in `template.yaml`, so a bundle edited after
  it was built is refused;
- routines arrive **paused**; plugins are only enabled when they are installed
  on this machine, and the rest are listed as missing;
- an existing profile is never overwritten unless it was installed from a
  template (`--force` updates that one, user data stays).

## What never travels

`.env`, `auth.json`, OAuth and platform sessions, cookies, browser profiles,
conversation history, `state.db`, logs, caches, workspace, `home/`, key material.
Every text file that does travel passes through Hermes' own secret redactor
(`agent.redact.redact_sensitive_text`) before it is written into the bundle.
`config.yaml` additionally loses `platforms`/`messaging`/`gateway`, `base_url`,
`api_mode`, and `model.provider`: the installer brings their own account.

## Desktop

Templates page (`/templates`): **Share** picks a profile, shows what travels,
packs, and offers the download plus the install command. **Install** takes a
link, a path, or a dropped file, previews it, and installs on confirmation.

## Rules of thumb

- Share a template, never a profile export: an export is a snapshot that can
  carry memories and conversation history.
- Tell the person what to reconnect (the preview lists the env keys).
- A template that needs a plugin they do not have is not broken — it says so.
